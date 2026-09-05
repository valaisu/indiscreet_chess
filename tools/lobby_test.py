"""
Balanced rooms, the open-game list and presence.

    python -m server.main --port 8765 &
    PYTHONPATH=. python3 tools/lobby_test.py

Balanced mode hands the two seats different numbers. That existed once and was
removed, because ROOM_STATE announced a single set of params and a joiner could
be seated into a crippled side without being shown it. So the assertions here
are not only "the handicap reaches the game" but "both columns are announced,
to the joiner, before anyone can ready" - the half that made it safe to bring
back. The rest covers the listing a room appears in and the presence count.
"""

import argparse
import asyncio
import json
import sys

import websockets

from server import presets
from shared import protocol

DEFAULT_URL = "ws://localhost:8765"

# A room where black refills mana faster and moves sooner. Distinctive values,
# so a snapshot carrying them proves they came from here.
WHITE_SIDE = {"mana_refill_rate": 0.30, "cooldown": 1.20, "maximum_mana": 6.0}
BLACK_SIDE = {"mana_refill_rate": 0.90, "cooldown": 0.40, "maximum_mana": 6.0}

FAILS: list[str] = []


async def recv(ws, kind: str, tries: int = 400) -> dict:
    for _ in range(tries):
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if msg.get("type") == kind:
            return msg
    raise AssertionError(f"no {kind} after {tries} messages")


async def playing(ws) -> dict:
    for _ in range(600):
        msg = await recv(ws, protocol.GAME_STATE)
        if msg["countdown"] is None:
            return msg
    raise AssertionError("countdown never finished")


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"[PASS] {name}")
    else:
        FAILS.append(f"{name}: {detail or 'failed'}")
        print(f"[FAIL] {name}: {detail}")


async def ready(ws, civ=None, color=None) -> None:
    msg = {"type": protocol.SET_READY, "ready": True, "civ": civ}
    if color:
        msg["color"] = color
    await ws.send(json.dumps(msg))


# --- balanced rooms ---------------------------------------------------------

async def test_balanced(url: str) -> None:
    print("\n-- a balanced room --")
    async with websockets.connect(url) as host, websockets.connect(url) as guest:
        await host.send(json.dumps({
            "type": protocol.CREATE_ROOM,
            "params": WHITE_SIDE,
            "params_black": BLACK_SIDE,
            "public": True,
        }))
        created = await recv(host, protocol.ROOM_CREATED)
        code = created["code"]
        check("host is seated white", created["color"] == "white", created["color"])

        await guest.send(json.dumps({"type": protocol.JOIN_ROOM, "code": code}))
        joined = await recv(guest, protocol.ROOM_JOINED)
        check("guest is seated black", joined["color"] == "black", joined["color"])

        # The whole point: the joiner is told both columns, before readying.
        state = await recv(guest, protocol.ROOM_STATE)
        check("the room says it is balanced", state.get("balanced") is True,
              str(state.get("balanced")))
        base = state.get("base_params") or {}
        check("the joiner is sent both columns",
              set(base) == {"white", "black"}, str(sorted(base)))
        check("white's column is white's",
              base.get("white", {}).get("mana_refill_rate") == 0.30,
              str(base.get("white")))
        check("black's column is black's, and the joiner can see it",
              base.get("black", {}).get("mana_refill_rate") == 0.90,
              str(base.get("black")))
        check("and they really are different",
              base["white"] != base["black"])

        await ready(host)
        await ready(guest)
        snap = await playing(guest)
        # The handicap has to survive into the running game, not just the lobby.
        cd = snap["cooldown"]
        check("the running game gives each side its own cooldown",
              cd["white"] == 1.20 and cd["black"] == 0.40, str(cd))
        # max_mana is filtered per viewer - enemy_mana is off by default - so
        # the guest sees its own pool and not white's. That the filtering still
        # applies to a balanced room is worth pinning: a handicap must not
        # become a way to read the other side's economy.
        mm = snap["max_mana"]
        check("a value both sides share is still shared",
              mm["black"] == 6.0, str(mm))
        check("and the opponent's mana pool is still hidden",
              "white" not in mm, str(mm))


async def test_even_room_is_not_balanced(url: str) -> None:
    print("\n-- an ordinary room --")
    async with websockets.connect(url) as host:
        await host.send(json.dumps({"type": protocol.CREATE_ROOM,
                                    "params": WHITE_SIDE}))
        await recv(host, protocol.ROOM_CREATED)
        state = await recv(host, protocol.ROOM_STATE)
        check("no params_black means an even room",
              state.get("balanced") is False, str(state.get("balanced")))
        base = state["base_params"]
        check("both columns are still sent, and match",
              base["white"] == base["black"], str(base))


async def test_black_column_is_validated(url: str) -> None:
    print("\n-- the second column is untrusted input too --")
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({
            "type": protocol.CREATE_ROOM,
            "params": {},
            "params_black": {"maximum_mana": 1e9},
        }))
        err = await recv(ws, protocol.ERROR)
        check("an out-of-range black column is rejected",
              "maximum_mana" in err["reason"], err["reason"])

    async with websockets.connect(url) as ws:
        # Piece size is checked against the real opening layout, per side.
        await ws.send(json.dumps({
            "type": protocol.CREATE_ROOM,
            "params": {},
            "params_black": {"diameter_piece": 1.4},
        }))
        err = await recv(ws, protocol.ERROR)
        check("a black column that starts overlapping is rejected",
              "touching" in err["reason"], err["reason"])


# --- the open-game list -----------------------------------------------------

async def test_room_list(url: str) -> None:
    print("\n-- finding an open game --")
    async with websockets.connect(url) as opener, \
               websockets.connect(url) as private, \
               websockets.connect(url) as looker:
        await opener.send(json.dumps({"type": protocol.CREATE_ROOM,
                                      "params": {}, "public": True}))
        open_code = (await recv(opener, protocol.ROOM_CREATED))["code"]

        await private.send(json.dumps({"type": protocol.CREATE_ROOM,
                                       "params": {}, "public": False}))
        private_code = (await recv(private, protocol.ROOM_CREATED))["code"]

        await looker.send(json.dumps({"type": protocol.LIST_ROOMS}))
        listing = await recv(looker, protocol.ROOM_LIST)
        codes = {r["code"] for r in listing["rooms"]}
        check("an open room is listed", open_code in codes, str(sorted(codes)))
        check("a private room is not", private_code not in codes,
              str(sorted(codes)))

        row = next(r for r in listing["rooms"] if r["code"] == open_code)
        check("the row carries the settings to choose by",
              set(row) >= {"tempo", "balanced", "base_params", "view", "waiting"},
              str(sorted(row)))
        check("an anonymous host is listed without a name",
              row["host"] is None, str(row["host"]))

        # Once it is full it is no longer somewhere to go.
        await looker.send(json.dumps({"type": protocol.JOIN_ROOM,
                                      "code": open_code}))
        await recv(looker, protocol.ROOM_JOINED)
        await private.send(json.dumps({"type": protocol.LIST_ROOMS}))
        listing = await recv(private, protocol.ROOM_LIST)
        check("a full room drops off the list",
              open_code not in {r["code"] for r in listing["rooms"]})


async def test_solo_is_never_listed(url: str) -> None:
    async with websockets.connect(url) as solo, websockets.connect(url) as looker:
        await solo.send(json.dumps({"type": protocol.CREATE_ROOM,
                                    "params": {}, "solo": True, "public": True}))
        code = (await recv(solo, protocol.ROOM_CREATED))["code"]
        await looker.send(json.dumps({"type": protocol.LIST_ROOMS}))
        listing = await recv(looker, protocol.ROOM_LIST)
        check("a solo room is never listed, even asking to be public",
              code not in {r["code"] for r in listing["rooms"]})


# --- quick match ------------------------------------------------------------

async def test_quick_match(url: str) -> None:
    """Two players asking for the same tempo end up in the same room.

    Including after one of them leaves and asks again, which is the bug this
    covers: leaving did not give the seat back, so the room the player had
    just left still looked full and they were sent to a second room while
    their opponent waited in the first.
    """
    print("\n-- quick match --")
    async with websockets.connect(url) as a, websockets.connect(url) as b:
        await a.send(json.dumps({"type": protocol.QUICK_MATCH, "tempo": "rapid"}))
        first = (await recv(a, protocol.ROOM_JOINED))["code"]

        await b.send(json.dumps({"type": protocol.QUICK_MATCH, "tempo": "rapid"}))
        second = (await recv(b, protocol.ROOM_JOINED))["code"]
        check("two players asking for the same tempo meet", first == second,
              f"{first} vs {second}")

        # The room the pair is in is the tempo they asked for, from the
        # server's own presets rather than anything either client sent.
        state = await recv(b, protocol.ROOM_STATE)
        check("the room is the tempo that was asked for",
              state.get("tempo") == "rapid", str(state.get("tempo")))
        # These two are anonymous, so the game will not be rated - but the
        # only thing standing in the way must be the sign-in, never anything
        # about the room quick match just built.
        check("nothing about a quick match room stops it being rated",
              state.get("unrated_reason") == "both players must be signed in",
              str(state.get("unrated_reason")))

        await a.send(json.dumps({"type": protocol.LEAVE_ROOM}))
        await a.send(json.dumps({"type": protocol.QUICK_MATCH, "tempo": "rapid"}))
        again = (await recv(a, protocol.ROOM_JOINED))["code"]
        check("leaving and asking again lands in the room just left",
              again == first, f"{first} -> {again}")

    async with websockets.connect(url) as c, websockets.connect(url) as d:
        await c.send(json.dumps({"type": protocol.QUICK_MATCH, "tempo": "bullet"}))
        bullet = (await recv(c, protocol.ROOM_JOINED))["code"]
        await d.send(json.dumps({"type": protocol.QUICK_MATCH, "tempo": "slow"}))
        slow = (await recv(d, protocol.ROOM_JOINED))["code"]
        check("different tempos do not get paired", bullet != slow,
              f"{bullet} vs {slow}")

    async with websockets.connect(url) as e:
        await e.send(json.dumps({"type": protocol.QUICK_MATCH, "tempo": "blitz"}))
        err = await recv(e, protocol.ERROR)
        check("an unknown tempo is refused", err["reason"] == "unknown tempo",
              err["reason"])


async def test_unrated_room(url: str) -> None:
    """The host can say a game does not count, and the room says so."""
    print("\n-- an unrated room --")
    async with websockets.connect(url) as host, websockets.connect(url) as guest:
        await host.send(json.dumps({"type": protocol.CREATE_ROOM,
                                    "params": {}, "public": True,
                                    "unrated": True}))
        code = (await recv(host, protocol.ROOM_CREATED))["code"]
        await guest.send(json.dumps({"type": protocol.JOIN_ROOM, "code": code}))
        await recv(guest, protocol.ROOM_JOINED)
        state = await recv(guest, protocol.ROOM_STATE)
        check("the joiner is told the room is unrated",
              state.get("rated") is False, str(state.get("rated")))
        check("and why", state.get("unrated_reason") == "the host chose an unrated game",
              str(state.get("unrated_reason")))

    # Quick match must not walk anyone into it: they asked for a tempo, not
    # for a game that does not count.
    async with websockets.connect(url) as host, websockets.connect(url) as looker:
        await host.send(json.dumps({"type": protocol.CREATE_ROOM,
                                    "params": presets.PRESETS["bullet"],
                                    "public": True, "unrated": True}))
        code = (await recv(host, protocol.ROOM_CREATED))["code"]
        await looker.send(json.dumps({"type": protocol.QUICK_MATCH,
                                      "tempo": "bullet"}))
        joined = await recv(looker, protocol.ROOM_JOINED)
        check("quick match does not join an unrated room",
              joined["code"] != code, f"{code} -> {joined['code']}")


# --- presence ---------------------------------------------------------------

async def test_presence(url: str) -> None:
    print("\n-- who is online --")
    async with websockets.connect(url) as a, websockets.connect(url) as b:
        await a.send(json.dumps({"type": protocol.LIST_ONLINE}))
        msg = await recv(a, protocol.ONLINE_LIST)
        check("the count includes both sockets", msg["count"] >= 2,
              str(msg["count"]))
        check("anonymous sockets are counted, not named",
              msg["users"] == [], str(msg["users"]))
        check("signed_in is reported", msg["signed_in"] == 0,
              str(msg["signed_in"]))


async def test_profile_needs_a_real_name(url: str) -> None:
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": protocol.GET_PROFILE,
                                  "name": "<script>alert(1)</script>"}))
        err = await recv(ws, protocol.ERROR)
        check("a bogus profile name is refused before it reaches the database",
              err["reason"] in ("no such player",
                               "accounts are not available on this server"),
              err["reason"])


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    args = ap.parse_args()

    await test_balanced(args.url)
    await test_even_room_is_not_balanced(args.url)
    await test_black_column_is_validated(args.url)
    await test_room_list(args.url)
    await test_solo_is_never_listed(args.url)
    await test_quick_match(args.url)
    await test_unrated_room(args.url)
    await test_presence(args.url)
    await test_profile_needs_a_real_name(args.url)

    if FAILS:
        print(f"\nFAIL: {len(FAILS)} check(s)")
        for f in FAILS:
            print(f"  {f}")
        return 1
    print("\nPASS: balanced rooms, quick match, the open-game list and presence")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
