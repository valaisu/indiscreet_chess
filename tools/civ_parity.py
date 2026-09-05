"""
Civilization parity: server/civs.py must resolve every civilization to exactly
the params web/src/civs.ts does.

    PYTHONPATH=. python3 tools/civ_parity.py

The client used to compute these and send them, and the server took the numbers
on trust - which meant a patched page could pick "roman" and play with no
cooldown at all. The server resolves them now, and the client only names its
pick. That leaves two copies of one table, so this asserts they agree: on the
preset tempos, on hand-edited ones, and on partial ones.

Edit web/src/civs.ts first. It is the copy with the reasoning written down.

Requires node >= 22.6 (TypeScript is stripped natively; no build step).
"""

import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

from server import civs, params, presets

ROOT = Path(__file__).resolve().parent.parent
CIVS_TS = ROOT / "web" / "src" / "civs.ts"
PRESETS_TS = ROOT / "web" / "src" / "presets.ts"

# What the client used to put in SET_READY, which is what the server now has to
# reproduce on its own: the tempo with the civ applied, plus absolute per-piece
# overrides when the civ has any.
RUNNER = """
import { withCiv, piecePayload } from %s;
let raw = "";
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {
  const cases = JSON.parse(raw);
  const out = cases.map(({ base, civ }) => {
    const withMods = withCiv(base, civ ?? "none");
    const pieces = piecePayload(withMods, civ ?? "none");
    return Object.keys(pieces).length ? { ...withMods, pieces } : withMods;
  });
  process.stdout.write(JSON.stringify(out));
});
"""

# The tempos a real room is opened with. One Python copy, in server/presets.py:
# this file is what proves it still matches web/src/presets.ts.
PRESETS = presets.PRESETS


def custom_bases(rng: random.Random, n: int) -> list[dict]:
    """Hand-edited tempos, and partial ones. A base that does not name a param
    must come back not naming it: the server fills those from its own defaults,
    and a civ that invented the key instead would change a value nobody set."""
    out = []
    for _ in range(n):
        base = {}
        for key, (lo, hi) in params.LIMITS.items():
            if rng.random() < 0.15:
                continue                      # left out of the tempo entirely
            base[key] = round(rng.uniform(lo, min(hi, lo + 5.0)), 3)
        out.append(base)
    return out


def run_node(cases: list[dict]) -> list[dict]:
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as fh:
        fh.write(RUNNER % json.dumps(str(CIVS_TS)))
        runner = fh.name
    try:
        proc = subprocess.run(
            ["node", "--experimental-strip-types", runner],
            input=json.dumps(cases).encode(),
            capture_output=True,
        )
    finally:
        Path(runner).unlink(missing_ok=True)
    if proc.returncode != 0:
        print(proc.stderr.decode()[:2000], file=sys.stderr)
        raise SystemExit("node runner failed")
    return json.loads(proc.stdout)


def main() -> int:
    rng = random.Random(7)
    bases = [(name, p) for name, p in PRESETS.items()]
    bases += [(f"custom{i}", b) for i, b in enumerate(custom_bases(rng, 20))]

    cases, labels = [], []
    for name, base in bases:
        for civ in (None,) + civs.CIV_NAMES:
            cases.append({"base": base, "civ": civ})
            labels.append((name, civ or "none"))

    expected = run_node(cases)

    failures = []
    for (label, case, want) in zip(labels, cases, expected):
        got = civs.resolve(case["base"], case["civ"])
        if got != want:
            failures.append((label, want, got))

    print(f"tempos={len(bases)} civs={len(civs.CIV_NAMES) + 1} "
          f"checked={len(cases)} failures={len(failures)}")

    for (tempo, civ), want, got in failures[:5]:
        differing = {k for k in set(want) | set(got) if want.get(k) != got.get(k)}
        print(f"  {tempo}/{civ}:")
        for key in sorted(differing):
            print(f"    {key}: civs.ts={want.get(key)!r} civs.py={got.get(key)!r}")

    if failures:
        print(f"FAIL: {len(failures)}/{len(cases)} resolutions disagree")
        return 1

    # The names themselves have to match, or a pick the client offers is one
    # the server rejects as unknown.
    ts_names = sorted(json.loads(subprocess.run(
        ["node", "--experimental-strip-types", "-e",
         f'import("{CIVS_TS}").then(m => '
         f'process.stdout.write(JSON.stringify(m.CIV_NAMES)))'],
        capture_output=True, check=True).stdout))
    if ts_names != sorted(civs.CIV_NAMES):
        print(f"FAIL: civ names differ: civs.ts={ts_names} civs.py={sorted(civs.CIV_NAMES)}")
        return 1

    # The tempo table is the base every civilization multiplies, and the server
    # now reads it directly to decide whether a game can be rated. A drift here
    # would rate games on numbers the two sides did not agree on.
    ts_presets = json.loads(subprocess.run(
        ["node", "--experimental-strip-types", "-e",
         f'import("{PRESETS_TS}").then(m => '
         f'process.stdout.write(JSON.stringify(m.PRESETS)))'],
        capture_output=True, check=True).stdout)
    if ts_presets != presets.PRESETS:
        differing = {k for k in set(ts_presets) | set(presets.PRESETS)
                     if ts_presets.get(k) != presets.PRESETS.get(k)}
        print(f"FAIL: tempo presets differ: {sorted(differing)}")
        for mode in sorted(differing):
            print(f"  presets.ts={ts_presets.get(mode)}")
            print(f"  presets.py={presets.PRESETS.get(mode)}")
        return 1

    print("PASS: both tables resolve every civilization identically, "
          "and the tempo presets match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
