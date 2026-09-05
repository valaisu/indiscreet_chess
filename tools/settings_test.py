"""
Personal settings: the two layers, and the two copies of their shape.

    PYTHONPATH=. python3 tools/settings_test.py

Settings live in two places on purpose. The device keeps its own values in
localStorage; a signed-in account keeps the ones it has an opinion about, and
those override the device's. This runs web/src/settings.ts under node and
checks that rule end to end, because the merge is easy to get right in one
direction and wrong in the other.

It also checks the second copy of the shape. server/user_settings.clean
decides what may be stored, and a key it does not recognise is silently
dropped, so a setting added to the client and forgotten here would save on the
device, appear to sync, and quietly never come back.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from server import user_settings

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_TS = ROOT / "web" / "src" / "settings.ts"

FAILS: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        FAILS.append(f"{name}:\n  got  {got!r}\n  want {want!r}")


# A device that has been set up: two keys, deliberately not the defaults.
DEVICE = {"moveMode": "drag", "dragThreshold": 0.1}

RUNNER = """
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};
localStorage.setItem("settings", %s);

// Dynamic, not a static import: the module reads localStorage as it loads, so
// the stub above has to be in place first.
const S = await import(%s);

const out = { defaults: S.DEFAULTS };
out.device_only = { ...S.settings };

// Signing in. One opinion the device has no value for, and one that
// contradicts it: without the contradiction the two merge orders agree and
// "the profile wins" passes whichever way round it is written.
S.applyProfile({ showHints: false, moveMode: "click" });
out.with_profile = { ...S.settings };

const published = [];
S.setPublisher((v) => published.push(structuredClone(v)));
S.save({ dragThreshold: 0.42 });
out.after_save = { ...S.settings };
out.published = published;
out.stored_device = JSON.parse(localStorage.getItem("settings"));

// Signing out.
S.applyProfile(null);
out.signed_out = { ...S.settings };

// An account that has never changed anything must not reset the browser.
S.applyProfile({});
out.empty_profile = { ...S.settings };

process.stdout.write(JSON.stringify(out));
"""


def run_node() -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as fh:
        fh.write(RUNNER % (json.dumps(json.dumps(DEVICE)),
                           json.dumps(str(SETTINGS_TS))))
        runner = fh.name
    try:
        proc = subprocess.run(["node", "--experimental-strip-types", runner],
                              capture_output=True)
    finally:
        Path(runner).unlink(missing_ok=True)
    if proc.returncode != 0:
        print(proc.stderr.decode()[:2000], file=sys.stderr)
        raise SystemExit("node runner failed")
    return json.loads(proc.stdout)


def main() -> int:
    r = run_node()
    defaults = r["defaults"]

    # -- the device layer on its own ----------------------------------------
    check("signed out, the device's values are in force",
          r["device_only"], {**defaults, **DEVICE})

    # -- the profile overrides it, key by key -------------------------------
    check("a profile's key wins over the device's",
          r["with_profile"],
          {**defaults, **DEVICE, "showHints": False, "moveMode": "click"})

    # -- a change while signed in is written to both ------------------------
    check("a change applies at once",
          r["after_save"]["dragThreshold"], 0.42)
    check("the device records it",
          r["stored_device"], {**DEVICE, "dragThreshold": 0.42})
    check("the account is sent only what it has an opinion about",
          r["published"], [{"showHints": False, "moveMode": "click",
                            "dragThreshold": 0.42}])

    # -- and signing out leaves the machine as its owner left it ------------
    # showHints goes back to the default and moveMode back to the device's
    # "drag": both opinions were the account's and neither was ever this
    # browser's. dragThreshold stays, because it was changed here and so the
    # device layer was written too.
    check("signing out drops back to the device",
          r["signed_out"], {**defaults, **DEVICE, "dragThreshold": 0.42})
    check("an account with no opinion changes nothing",
          r["empty_profile"], r["signed_out"])

    # -- the server's copy of the shape -------------------------------------
    # Every setting the client can hold must survive the trip, at its default
    # value and at a plausible edited one.
    check("the server stores every setting the client has",
          user_settings.clean(defaults), defaults)
    edited = {"moveMode": "click", "dragThreshold": 0.0, "showHints": False,
              "preciseKey": "q", "unselectKey": " "}
    check("and every setting at an edited value",
          user_settings.clean(edited), edited)
    check("a partial object stays partial",
          user_settings.clean({"showHints": True}), {"showHints": True})

    # -- and refuses what a patched client could send -----------------------
    junk = {"moveMode": "teleport", "dragThreshold": float("nan"),
            "showHints": "yes", "preciseKey": "x" * 21, "unselectKey": "",
            "sixthSetting": {"deeply": ["nested", 1, 2, 3]}}
    check("junk is dropped, key by key", user_settings.clean(junk), {})
    check("a true is not a drag threshold",
          user_settings.clean({"dragThreshold": True}), {})
    check("a threshold longer than the board is refused",
          user_settings.clean({"dragThreshold": 9.0}), {})
    check("a non-object is not settings", user_settings.clean([1, 2]), {})

    print()
    for f in FAILS:
        print("FAIL", f)
    print(f"{len(FAILS)} failure(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
