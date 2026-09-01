"""
Headless test client. There is no browser in CI and no test suite yet, so this
is how the room layer gets verified.

    python -m tools.fake_client --pair              # two clients, one game
    python -m tools.fake_client --pair --rooms 3    # concurrent rooms
    python -m tools.fake_client --hostile           # abuse checks
"""

import argparse
import asyncio
import json
import random
import sys

import websockets

from shared import protocol

DEFAULT_URL = "ws://localhost:8765"


def legal_destination(piece: dict) -> tuple[float, float] | None:
    """
    A destination that server/rules.py should accept. Deliberately naive — it
    only needs to generate traffic, not play well. Exact directions are always
    inside the freedom cone.
    """
    x, y, kind = piece["x"], piece["y"], piece["type"]
    fwd = -1.0 if piece["owner"] == "white" else 1.0

    if kind == "pawn":
        cands = [(x, y + fwd)]
    elif kind == "knight":
        cands = [(x + dx, y + dy) for dx, dy in
                 ((2, 1), (2, -1), (-2, 1), (-2, -1), (1, 2), (1, -2), (-1, 2), (-1, -2))]
    elif kind == "king":
        cands = [(x + dx, y + dy) for dx, dy in
                 ((1, 0), (-1, 0), (0, 1), (0, -1))]
    else:
        step = random.choice((1.0, 2.0, 3.0))
        ortho = [(step, 0), (-step, 0), (0, step), (0, -step)]
        diag = [(step, step), (step, -step), (-step, step), (-step, -step)]
        dirs = {"rook": ortho, "bishop": diag}.get(kind, ortho + diag)
        cands = [(x + dx, y + dy) for dx, dy in dirs]

    cands = [(cx, cy) for cx, cy in cands if 0.35 < cx < 7.65 and 0.35 < cy < 7.65]
    return random.choice(cands) if cands else None


class FakeClient:
    def __init__(self, url: str, name: str) -> None:
        self.url = url
        self.name = name
        self.color: str | None = None
        self.code: str | None = None
        self.token: str | None = None
        self.ticks = 0
        self.accepted = 0
        self.rejected = 0
        self.errors: list[str] = []
        self.winner: str | None = None
        self.game_over = asyncio.Event()
        self.room_ready = asyncio.Event()
        self.ws = None

    async def send(self, msg: dict) -> None:
        await self.ws.send(json.dumps(msg))

    async def run(self, action: str, code: str | None, duration: float) -> None:
        async with websockets.connect(self.url) as ws:
            self.ws = ws
            if action == "create":
                await self.send({"type": protocol.CREATE_ROOM, "params": {}})
            elif action == "join":
                await self.send({"type": protocol.JOIN_ROOM, "code": code})
            elif action == "quick":
                await self.send({"type": protocol.QUICK_MATCH, "params": {}})

            reader = asyncio.create_task(self._read())
            mover = asyncio.create_task(self._move_loop())
            try:
                await asyncio.wait_for(self.game_over.wait(), timeout=duration)
            except asyncio.TimeoutError:
                pass
            finally:
                for t in (reader, mover):
                    t.cancel()

    async def _read(self) -> None:
        async for raw in self.ws:
            msg = json.loads(raw)
            kind = msg.get("type")
            if kind == protocol.GAME_STATE:
                self.ticks += 1
                self.state = msg
                if msg.get("game_over"):
                    self.winner = msg.get("winner")
                    self.game_over.set()
            elif kind in (protocol.ROOM_CREATED, protocol.ROOM_JOINED):
                self.code = msg.get("code")
                self.color = msg.get("color")
                self.token = msg.get("token")
                self.room_ready.set()
            elif kind == protocol.MOVE_REJECTED:
                self.rejected += 1
            elif kind == protocol.ERROR:
                self.errors.append(msg.get("reason", "?"))
                self.room_ready.set()
            elif kind == protocol.OPPONENT_LEFT:
                self.errors.append("opponent_left")

    async def _move_loop(self) -> None:
        self.state: dict | None = None
        while True:
            await asyncio.sleep(0.25)
            state = getattr(self, "state", None)
            if not state or state.get("countdown") is not None:
                continue
            mine = [p for p in state["pieces"]
                    if p["state"] == "idle" and p["type"] != "ghost"
                    and (p["owner"] == self.color)]
            if not mine:
                continue
            piece = random.choice(mine)
            dest = legal_destination(piece)
            if dest is None:
                continue
            await self.send({"type": protocol.QUEUE_MOVE, "piece_id": piece["id"],
                             "destination": list(dest)})
            self.accepted += 1


async def run_pair(url: str, duration: float, index: int) -> dict:
    host = FakeClient(url, f"host{index}")
    guest = FakeClient(url, f"guest{index}")

    host_task = asyncio.create_task(host.run("create", None, duration))
    await asyncio.wait_for(host.room_ready.wait(), timeout=5)
    if host.code is None:
        return {"ok": False, "why": f"no room code: {host.errors}"}

    guest_task = asyncio.create_task(guest.run("join", host.code, duration))
    await asyncio.gather(host_task, guest_task)

    return {
        "ok": host.ticks > 0 and guest.ticks > 0 and host.color != guest.color,
        "code": host.code,
        "colors": (host.color, guest.color),
        "ticks": (host.ticks, guest.ticks),
        "moves_sent": host.accepted + guest.accepted,
        "rejected": host.rejected + guest.rejected,
        "winner": host.winner,
        "errors": host.errors + guest.errors,
    }


async def _await_msg(ws, kinds: set[str], timeout: float) -> dict | None:
    """Read until one of `kinds` arrives, or give up."""
    async def pump():
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") in kinds:
                return msg
        return None
    try:
        return await asyncio.wait_for(pump(), timeout=timeout)
    except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
        return None


async def run_disconnect(url: str, grace: float) -> list[tuple[str, bool, str]]:
    """Drop one player mid-game; the other should be told, then win by forfeit."""
    results = []
    async with websockets.connect(url) as host:
        await host.send(json.dumps({"type": protocol.CREATE_ROOM, "params": {}}))
        created = await _await_msg(host, {protocol.ROOM_CREATED}, 5)
        code = created["code"]

        guest = await websockets.connect(url)
        await guest.send(json.dumps({"type": protocol.JOIN_ROOM, "code": code}))
        joined = await _await_msg(guest, {protocol.ROOM_JOINED}, 5)
        results.append(("seats differ", created["color"] != joined["color"],
                        f"{created['color']} vs {joined['color']}"))

        await _await_msg(host, {protocol.GAME_STATE}, 8)
        await guest.close()

        left = await _await_msg(host, {protocol.OPPONENT_LEFT}, 5)
        results.append(("opponent_left sent", left is not None,
                        f"grace={left.get('grace_seconds') if left else '-'}"))

        over = None
        deadline = asyncio.get_event_loop().time() + grace + 8
        while asyncio.get_event_loop().time() < deadline:
            msg = await _await_msg(host, {protocol.GAME_STATE}, 5)
            if msg is None:
                break
            if msg.get("game_over"):
                over = msg
                break
        results.append(("forfeit awards win", over is not None
                        and over.get("winner") == created["color"],
                        f"winner={over.get('winner') if over else 'none'}"))
    return results


async def run_rejoin(url: str, grace: float) -> list[tuple[str, bool, str]]:
    """Drop a player and reclaim the seat with the session token inside grace."""
    results = []
    async with websockets.connect(url) as host:
        await host.send(json.dumps({"type": protocol.CREATE_ROOM, "params": {}}))
        created = await _await_msg(host, {protocol.ROOM_CREATED}, 5)
        code = created["code"]

        guest = await websockets.connect(url)
        await guest.send(json.dumps({"type": protocol.JOIN_ROOM, "code": code}))
        joined = await _await_msg(guest, {protocol.ROOM_JOINED}, 5)
        token = joined["token"]

        await _await_msg(host, {protocol.GAME_STATE}, 8)
        await guest.close()
        await _await_msg(host, {protocol.OPPONENT_LEFT}, 5)

        await asyncio.sleep(min(1.0, grace / 3))
        guest2 = await websockets.connect(url)
        await guest2.send(json.dumps({"type": protocol.REJOIN, "code": code,
                                      "token": token}))
        back = await _await_msg(guest2, {protocol.ROOM_JOINED, protocol.ERROR}, 5)
        results.append(("rejoin accepted",
                        back is not None and back.get("type") == protocol.ROOM_JOINED,
                        str(back.get("reason") if back else "no reply")))
        results.append(("same seat returned",
                        back is not None and back.get("color") == joined["color"],
                        f"{joined['color']} -> {back.get('color') if back else '-'}"))

        state = await _await_msg(guest2, {protocol.GAME_STATE}, 5)
        results.append(("game still live", state is not None
                        and not state.get("game_over"), "receiving state"))

        # Past the original grace window the forfeit must not fire late.
        await asyncio.sleep(grace + 1.0)
        state = await _await_msg(guest2, {protocol.GAME_STATE}, 5)
        results.append(("no late forfeit", state is not None
                        and not state.get("game_over"),
                        f"winner={state.get('winner') if state else 'no state'}"))
        await guest2.close()
    return results


async def run_hostile(url: str) -> list[tuple[str, bool, str]]:
    results = []

    async def expect_error(label: str, msg: dict) -> None:
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps(msg))
            try:
                reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                ok = reply.get("type") == protocol.ERROR
                results.append((label, ok, reply.get("reason", reply.get("type", "?"))))
            except asyncio.TimeoutError:
                results.append((label, False, "no reply"))

    await expect_error("out-of-range param",
                       {"type": protocol.CREATE_ROOM, "params": {"maximum_mana": 1e9}})
    await expect_error("unknown param",
                       {"type": protocol.CREATE_ROOM, "params": {"tick_rate": 9999}})
    await expect_error("nan param",
                       {"type": protocol.CREATE_ROOM, "params": {"movement_speed": None}})
    await expect_error("join missing room",
                       {"type": protocol.JOIN_ROOM, "code": "ZZZZ"})
    await expect_error("unknown message type", {"type": "NONSENSE"})
    await expect_error("rejoin bad token",
                       {"type": protocol.REJOIN, "code": "ZZZZ", "token": "x"})

    # Malformed JSON must not kill the connection.
    async with websockets.connect(url) as ws:
        await ws.send("{not json")
        reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        results.append(("malformed json", reply.get("type") == protocol.ERROR,
                        reply.get("reason", "?")))

    # Flood: the server should close the socket rather than fall over.
    try:
        async with websockets.connect(url) as ws:
            for _ in range(200):
                await ws.send(json.dumps({"type": protocol.PING, "t": 0}))
            await asyncio.sleep(1.0)
            await ws.send(json.dumps({"type": protocol.PING, "t": 0}))
            results.append(("flood closed", False, "still open"))
    except websockets.exceptions.ConnectionClosed:
        results.append(("flood closed", True, "connection closed"))

    return results


async def amain() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--pair", action="store_true")
    ap.add_argument("--hostile", action="store_true")
    ap.add_argument("--disconnect", action="store_true")
    ap.add_argument("--rejoin", action="store_true")
    ap.add_argument("--grace", type=float, default=3.0,
                    help="must match the server's --grace")
    ap.add_argument("--rooms", type=int, default=1)
    ap.add_argument("--duration", type=float, default=8.0)
    args = ap.parse_args()

    failed = False

    if args.pair:
        results = await asyncio.gather(
            *(run_pair(args.url, args.duration, i) for i in range(args.rooms))
        )
        codes = [r.get("code") for r in results]
        for r in results:
            status = "PASS" if r["ok"] else "FAIL"
            failed |= not r["ok"]
            print(f"[{status}] room {r.get('code')} colors={r.get('colors')} "
                  f"ticks={r.get('ticks')} moves={r.get('moves_sent')} "
                  f"rejected={r.get('rejected')} winner={r.get('winner')} "
                  f"errors={r.get('errors')}")
        if len(set(codes)) != len(codes):
            print("[FAIL] duplicate room codes issued")
            failed = True
        else:
            print(f"[PASS] {len(codes)} distinct room code(s)")

    for enabled, runner in ((args.disconnect, run_disconnect),
                            (args.rejoin, run_rejoin)):
        if enabled:
            for label, ok, detail in await runner(args.url, args.grace):
                failed |= not ok
                print(f"[{'PASS' if ok else 'FAIL'}] {label}: {detail}")

    if args.hostile:
        for label, ok, detail in await run_hostile(args.url):
            failed |= not ok
            print(f"[{'PASS' if ok else 'FAIL'}] {label}: {detail}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
