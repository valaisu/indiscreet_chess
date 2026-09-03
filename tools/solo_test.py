"""
Solo practice with a civilization per seat, and resignation.

    python -m server.main --port 8765 &
    PYTHONPATH=. python3 tools/solo_test.py

Both features are room-lifecycle changes, which is the part of this server that
has broken quietly before: a solo client readies two seats with one socket, and
a resignation ends a running game without a disconnect. Neither is visible from
the pawn or visibility tests.
"""

import argparse
import asyncio
import json
import sys

import websockets

from shared import protocol

DEFAULT_URL = "ws://localhost:8765"

# Enough of a difference between the seats to be unmistakable in a snapshot.
WHITE_PARAMS = {"maximum_mana": 6.0, "cooldown": 0.8}
BLACK_PARAMS = {"maximum_mana": 4.0, "cooldown": 1.2}


async def recv(ws, kind: str, tries: int = 400) -> dict:
    """Next message of this type. Everything before it is ignored, so a new
    unsolicited frame (SERVER_HELLO was one) cannot break the test."""
    for _ in range(tries):
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if msg.get("type") == kind:
            return msg
    raise AssertionError(f"no {kind} after {tries} messages")


async def playing(ws) -> dict:
    """First snapshot with the countdown finished."""
    for _ in range(600):
        msg = await recv(ws, protocol.GAME_STATE)
        if msg["countdown"] is None:
            return msg
    raise AssertionError("countdown never finished")


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail or 'failed'}")
    print(f"[PASS] {name}")


async def test_solo_two_seats(url: str) -> None:
    """One client, both seats, a different civilization on each."""
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": protocol.CREATE_ROOM, "solo": True,
                                  "params": WHITE_PARAMS}))
        created = await recv(ws, protocol.ROOM_CREATED)
        check("solo room announces itself as solo", created.get("solo") is True)

        for color, params, civ in (("white", WHITE_PARAMS, "hun"),
                                   ("black", BLACK_PARAMS, "norse")):
            await ws.send(json.dumps({"type": protocol.SET_READY, "ready": True,
                                      "color": color, "civ": civ, "params": params}))

        state = await playing(ws)
        check("each seat kept its own civilization",
              state["civs"] == {"white": "hun", "black": "norse"}, str(state["civs"]))
        check("and its own parameters",
              (state["max_mana"]["white"], state["max_mana"]["black"]) == (6.0, 4.0),
              str(state["max_mana"]))

        # The point of solo: moving a black piece from the seat dealt white.
        for owner in ("white", "black"):
            pawn = next(p for p in state["pieces"]
                        if p["owner"] == owner and p["type"] == "pawn")
            step = -1.0 if owner == "white" else 1.0
            await ws.send(json.dumps({"type": protocol.QUEUE_MOVE,
                                      "piece_id": pawn["id"],
                                      "destination": [pawn["x"], pawn["y"] + step]}))
            moved = False
            for _ in range(120):
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if msg.get("type") == protocol.MOVE_REJECTED:
                    raise AssertionError(f"{owner} move rejected: {msg.get('reason')}")
                if msg.get("type") == protocol.GAME_STATE:
                    now = next(p for p in msg["pieces"] if p["id"] == pawn["id"])
                    if now["state"] != "idle":
                        moved = True
                        break
            check(f"a {owner} piece accepts a move from the solo client", moved)


async def test_solo_leave_ends_room(url: str) -> None:
    """Leaving a solo room stops it. A solo client is seated twice, so clearing
    only the seat it was dealt left the other pointing at the same socket: the
    game kept running and kept sending, which pulled the player back out of the
    lobby and onto the board."""
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": protocol.CREATE_ROOM, "solo": True,
                                  "params": {}}))
        await recv(ws, protocol.ROOM_CREATED)
        for color in ("white", "black"):
            await ws.send(json.dumps({"type": protocol.SET_READY, "ready": True,
                                      "color": color, "civ": None, "params": {}}))
        await playing(ws)

        await ws.send(json.dumps({"type": protocol.LEAVE_ROOM}))
        # Frames already in flight are fine; a second of them is not.
        deadline = asyncio.get_running_loop().time() + 1.5
        late = 0
        while asyncio.get_running_loop().time() < deadline:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.3))
            except asyncio.TimeoutError:
                continue
            if msg.get("type") == protocol.GAME_STATE:
                late += 1
        check("a left solo room stops sending", late <= 3, f"{late} late frames")


async def test_resign(url: str) -> None:
    """A resignation ends the game for both seats, without a disconnect."""
    async with websockets.connect(url) as white, websockets.connect(url) as black:
        await white.send(json.dumps({"type": protocol.CREATE_ROOM, "params": {}}))
        created = await recv(white, protocol.ROOM_CREATED)
        await black.send(json.dumps({"type": protocol.JOIN_ROOM, "code": created["code"]}))
        await recv(black, protocol.ROOM_JOINED)

        # Resigning before the game runs must be ignored, not crash the room.
        await white.send(json.dumps({"type": protocol.RESIGN}))

        for ws in (white, black):
            await ws.send(json.dumps({"type": protocol.SET_READY, "ready": True,
                                      "civ": None, "params": {}}))
        state = await playing(white)
        check("the room started despite the early resign",
              state["game_over"] is False)

        await white.send(json.dumps({"type": protocol.RESIGN}))
        for name, ws in (("white", white), ("black", black)):
            for _ in range(600):
                msg = await recv(ws, protocol.GAME_STATE)
                if msg["game_over"]:
                    check(f"{name} sees black win by resignation",
                          msg["winner"] == "black", str(msg["winner"]))
                    break
            else:
                raise AssertionError(f"{name} never saw the game end")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL)
    args = ap.parse_args()

    try:
        await test_solo_two_seats(args.url)
        await test_solo_leave_ends_room(args.url)
        await test_resign(args.url)
    except AssertionError as err:
        print(f"FAIL: {err}", file=sys.stderr)
        raise SystemExit(1)
    print("PASS: solo seats and resignation")


if __name__ == "__main__":
    asyncio.run(main())
