"""
Headless test client. There is no browser in CI and no test suite yet, so this
is how the room layer gets verified.

    python -m tools.fake_client --pair              # two clients, one game
    python -m tools.fake_client --pair --rooms 3    # concurrent rooms
    python -m tools.fake_client --hostile           # abuse checks
    python -m tools.fake_client --accounts          # sign in, rated game
"""

import argparse
import asyncio
import json
import random
import secrets
import sys

import websockets

from shared import protocol

DEFAULT_URL = "ws://localhost:8765"

# A deployed server restricts Origin, and non-browser clients send none, so
# testing production means declaring which page we are standing in for.
ORIGIN: str | None = None


def _connect(url: str):
    return websockets.connect(url, origin=ORIGIN) if ORIGIN else websockets.connect(url)


def legal_destination(piece: dict) -> tuple[float, float] | None:
    """
    A destination that server/rules.py should accept. Deliberately naive - it
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
        try:
            await self.ws.send(json.dumps(msg))
        except websockets.exceptions.ConnectionClosed as exc:
            self.errors.append(f"closed: {exc.rcvd.reason if exc.rcvd else exc}")
            self.room_ready.set()
            self.game_over.set()

    async def run(self, action: str, code: str | None, duration: float) -> None:
        try:
            await self._run(action, code, duration)
        except websockets.exceptions.ConnectionClosed as exc:
            self.errors.append(f"closed: {exc.rcvd.reason if exc.rcvd else exc}")
        except OSError as exc:
            self.errors.append(f"connect failed: {exc}")
        finally:
            self.room_ready.set()
            self.game_over.set()

    async def _run(self, action: str, code: str | None, duration: float) -> None:
        async with _connect(self.url) as ws:
            self.ws = ws
            if action == "create":
                await self.send({"type": protocol.CREATE_ROOM, "params": {}})
            elif action == "join":
                await self.send({"type": protocol.JOIN_ROOM, "code": code})
            elif action == "quick":
                # A tempo name, not params: the server builds the room from
                # its own presets so both players know what they agreed to.
                await self.send({"type": protocol.QUICK_MATCH, "tempo": "bullet"})

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
                # Rooms wait for both seats to confirm before starting, so a
                # bot that never readies would just sit in the pre-game screen.
                await self.send({"type": protocol.SET_READY, "ready": True,
                                 "civ": None})
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
        await host_task
        why = "; ".join(host.errors) or "no reply"
        if "too many connections" in why:
            why += "  (raise the server's --max-conn-per-ip for local load tests)"
        return {"ok": False, "why": f"room {index}: {why}"}

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


async def _ready(ws) -> None:
    """Confirm a seat. Rooms no longer start until both sides have."""
    await ws.send(json.dumps({"type": protocol.SET_READY, "ready": True,
                              "civ": None}))


async def _await_state(ws, matches, timeout: float) -> dict | None:
    """Read until a ROOM_STATE `matches` describes, or give up.

    A socket that has been sitting in a room has several older ROOM_STATEs
    queued from the readying that started the game, so "the next ROOM_STATE"
    is not the one a button press produced. Reading up to the state that shows
    the effect being tested skips those, and a test whose effect never arrives
    fails on a None rather than passing on a stale message - which is how the
    old rematch check passed while asserting the opposite of what it meant.
    """
    async def pump():
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == protocol.ROOM_STATE and matches(msg):
                return msg
        return None
    try:
        return await asyncio.wait_for(pump(), timeout=timeout)
    except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
        return None


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
    async with _connect(url) as host:
        await host.send(json.dumps({"type": protocol.CREATE_ROOM, "params": {}}))
        created = await _await_msg(host, {protocol.ROOM_CREATED}, 5)
        code = created["code"]

        guest = await _connect(url)
        await guest.send(json.dumps({"type": protocol.JOIN_ROOM, "code": code}))
        joined = await _await_msg(guest, {protocol.ROOM_JOINED}, 5)
        results.append(("seats differ", created["color"] != joined["color"],
                        f"{created['color']} vs {joined['color']}"))
        await _ready(host)
        await _ready(guest)

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
    async with _connect(url) as host:
        await host.send(json.dumps({"type": protocol.CREATE_ROOM, "params": {}}))
        created = await _await_msg(host, {protocol.ROOM_CREATED}, 5)
        code = created["code"]

        guest = await _connect(url)
        await guest.send(json.dumps({"type": protocol.JOIN_ROOM, "code": code}))
        joined = await _await_msg(guest, {protocol.ROOM_JOINED}, 5)
        token = joined["token"]
        await _ready(host)
        await _ready(guest)

        await _await_msg(host, {protocol.GAME_STATE}, 8)
        await guest.close()
        await _await_msg(host, {protocol.OPPONENT_LEFT}, 5)

        await asyncio.sleep(min(1.0, grace / 3))
        guest2 = await _connect(url)
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


async def run_lobby_seat(url: str, grace: float) -> list[tuple[str, bool, str]]:
    """A seat left empty in the lobby comes back to the room.

    The seat used to be held by its token for as long as the room lived, which
    is right while a game is being played and wrong once it is not. A player
    whose phone slept lost the token with the page, so their own reservation
    answered "room is full" while the player still waiting was told "not here
    yet" forever.

    Inside the grace window the seat is still theirs - that is what makes a
    reload work - so both halves are checked here.
    """
    results = []
    async with _connect(url) as host:
        await host.send(json.dumps({"type": protocol.CREATE_ROOM, "params": {}}))
        created = await _await_msg(host, {protocol.ROOM_CREATED}, 5)
        code = created["code"]

        guest = await _connect(url)
        await guest.send(json.dumps({"type": protocol.JOIN_ROOM, "code": code}))
        joined = await _await_msg(guest, {protocol.ROOM_JOINED}, 5)
        await guest.close()

        # Straight away the seat is still held, or a reload could not reclaim it.
        async with _connect(url) as early:
            await early.send(json.dumps({"type": protocol.JOIN_ROOM, "code": code}))
            reply = await _await_msg(early, {protocol.ROOM_JOINED, protocol.ERROR}, 5)
            results.append(("the seat is held during the grace window",
                            reply is not None and reply.get("type") == protocol.ERROR,
                            str(reply.get("reason") if reply else "no reply")))

        await asyncio.sleep(grace + 1.5)

        async with _connect(url) as late:
            await late.send(json.dumps({"type": protocol.JOIN_ROOM, "code": code}))
            reply = await _await_msg(late, {protocol.ROOM_JOINED, protocol.ERROR}, 5)
            results.append(("the seat is free once the grace window passes",
                            reply is not None
                            and reply.get("type") == protocol.ROOM_JOINED,
                            str(reply.get("reason") if reply else "no reply")))
            results.append(("the same colour is handed out again",
                            reply is not None and reply.get("color") == joined["color"],
                            f"{joined['color']} -> {reply.get('color') if reply else '-'}"))
    return results


async def run_rematch(url: str) -> list[tuple[str, bool, str]]:
    """Finish a game by resigning, then play the same room again.

    A rematch takes both players, like readying up: one press must not drag
    the other player off the result they are still reading. The press after
    that lands on a room already back in the lobby, and must not come back as
    an error on the slower player's screen.
    """
    results = []
    async with _connect(url) as host:
        await host.send(json.dumps({"type": protocol.CREATE_ROOM, "params": {}}))
        created = await _await_msg(host, {protocol.ROOM_CREATED}, 5)
        code = created["code"]

        guest = await _connect(url)
        await guest.send(json.dumps({"type": protocol.JOIN_ROOM, "code": code}))
        await _await_msg(guest, {protocol.ROOM_JOINED}, 5)
        await _ready(host)
        await _ready(guest)
        results.append(("first game starts",
                        await _await_msg(host, {protocol.GAME_STATE}, 8) is not None, ""))

        await host.send(json.dumps({"type": protocol.RESIGN}))
        over = None
        for _ in range(40):
            msg = await _await_msg(host, {protocol.GAME_STATE}, 5)
            if msg is None or msg.get("game_over"):
                over = msg
                break
        results.append(("resign ends it", over is not None and over.get("game_over"),
                        f"winner={over.get('winner') if over else 'none'}"))

        # One press is an offer, not a decision. Read up to the state that
        # records it: if the press reopened the room instead, the flag was
        # cleared by the reset and no such state ever arrives.
        await host.send(json.dumps({"type": protocol.REMATCH}))
        asked = await _await_state(
            guest, lambda s: s.get("seats", {}).get("white", {}).get("rematch"), 5)
        seats = (asked or {}).get("seats", {})
        results.append(("one press does not reopen the room",
                        asked is not None and asked.get("waiting") is False,
                        f"waiting={asked.get('waiting') if asked else 'none'}"))
        results.append(("the asking seat is the only one marked",
                        seats.get("white", {}).get("rematch") is True
                        and seats.get("black", {}).get("rematch") is False,
                        f"seats={ {c: s.get('rematch') for c, s in seats.items()} }"))

        # The second press is the agreement, and reopens the room.
        await guest.send(json.dumps({"type": protocol.REMATCH}))
        back = await _await_state(guest, lambda s: s.get("waiting") is True, 5)
        results.append(("both presses reopen the room",
                        back is not None and back.get("waiting") is True,
                        f"waiting={back.get('waiting') if back else 'none'}"))
        results.append(("readiness is cleared",
                        back is not None and not any(back.get("ready", {}).values()),
                        f"ready={back.get('ready') if back else 'none'}"))
        results.append(("the rematch flags are cleared",
                        back is not None and not any(
                            s.get("rematch") for s in back.get("seats", {}).values()),
                        f"seats={back.get('seats') if back else 'none'}"))

        # A third press arrives at a room that is already in the lobby.
        await guest.send(json.dumps({"type": protocol.REMATCH}))
        echo = await _await_msg(guest, {protocol.ROOM_STATE, protocol.ERROR}, 5)
        results.append(("a press at an open room is not an error",
                        echo is not None and echo.get("type") == protocol.ROOM_STATE,
                        f"got={echo.get('type') if echo else 'none'}"
                        f" {echo.get('reason', '') if echo else ''}"))

        await _ready(host)
        await _ready(guest)
        results.append(("second game starts",
                        await _await_msg(host, {protocol.GAME_STATE}, 8) is not None, ""))
        await guest.close()
    return results


async def run_accounts(url: str) -> list[tuple[str, bool, str]]:
    """Two signed-in players finish a rated game and both ratings move.

    The only test that covers the whole chain at once: sign-up, the identity
    riding on the seat, the standard tempo making the game ratable, the save
    off the game loop, and the RATING_UPDATE that follows it. Requires the
    server to have a database; without one it reports that and passes nothing.
    """
    results = []
    tag = secrets.token_hex(3)
    rapid = {"mana_refill_rate": 0.15, "maximum_mana": 5.0, "base_move_cost": 1.0,
             "distance_cost": 0.2, "preparation_period": 0.5, "cooldown": 1.3,
             "movement_speed": 2.0, "movement_freedom_deg": 5.0,
             "diameter_piece": 0.6}

    async def sign_up(ws, name):
        await ws.send(json.dumps({"type": protocol.SIGN_UP, "name": name,
                                  "password": "a test password"}))
        return await _await_msg(ws, {protocol.AUTH_STATE, protocol.AUTH_ERROR}, 10)

    async with _connect(url) as host:
        who = await sign_up(host, f"t{tag}w")
        if who is None or who.get("type") == protocol.AUTH_ERROR:
            reason = (who or {}).get("reason", "no reply")
            results.append(("accounts available", False, reason))
            return results
        results.append(("host signs up", who["user"]["name"] == f"t{tag}w",
                        who["user"]["name"]))
        results.append(("no ratings before playing", who["user"]["ratings"] == {},
                        str(who["user"]["ratings"])))

        guest = await _connect(url)
        g = await sign_up(guest, f"t{tag}b")
        results.append(("guest signs up", g.get("type") == protocol.AUTH_STATE, ""))

        # A preset tempo, both signed in, not solo: this one is ratable.
        await host.send(json.dumps({"type": protocol.CREATE_ROOM, "params": rapid}))
        created = await _await_msg(host, {protocol.ROOM_CREATED}, 5)
        await guest.send(json.dumps({"type": protocol.JOIN_ROOM,
                                     "code": created["code"]}))
        await _await_msg(guest, {protocol.ROOM_JOINED}, 5)
        await _ready(host)
        await _ready(guest)
        results.append(("rated game starts",
                        await _await_msg(host, {protocol.GAME_STATE}, 10) is not None, ""))

        await guest.send(json.dumps({"type": protocol.RESIGN}))

        # Where the game was stored. Both seats are told, because that
        # recording is what the replay button asks for: the frames a client
        # kept are only the half of the game it was allowed to see.
        saved = await _await_msg(host, {protocol.GAME_SAVED}, 20)
        results.append(("the stored game is announced",
                        saved is not None and bool(saved.get("game_id")),
                        str(saved.get("game_id") if saved else "no GAME_SAVED")))
        results.append(("the other seat is told too",
                        await _await_msg(guest, {protocol.GAME_SAVED}, 10) is not None,
                        ""))

        # And it can be fetched back, in full, by a player who was in it.
        if saved is not None:
            await host.send(json.dumps({"type": protocol.GET_GAME,
                                        "id": saved["game_id"]}))
            record = await _await_msg(host, {protocol.GAME_RECORD, protocol.ERROR}, 10)
            ok = record is not None and record.get("type") == protocol.GAME_RECORD
            results.append(("the stored recording comes back", ok,
                            str(record.get("reason") if record else "no reply")))
            # The header carries both players' mana and both civilizations,
            # which is what makes a replay show the whole game rather than
            # the half this client was sent while playing.
            header = ((record or {}).get("recording") or {}).get("header") or {}
            results.append(("it holds both sides",
                            set(header.get("mana", {})) == {"white", "black"}
                            and set(header.get("civs", {})) == {"white", "black"},
                            f"mana={sorted(header.get('mana', {}))} "
                            f"civs={sorted(header.get('civs', {}))}"))

        moved = await _await_msg(host, {protocol.RATING_UPDATE}, 20)
        if moved is None:
            results.append(("rating update arrives", False, "no RATING_UPDATE"))
            await guest.close()
            return results

        results.append(("rating update arrives", True, moved["tempo"]))
        results.append(("winner gains",
                        moved["white"]["after"] > moved["white"]["before"],
                        f'{moved["white"]["before"]} -> {moved["white"]["after"]}'))
        results.append(("loser drops",
                        moved["black"]["after"] < moved["black"]["before"],
                        f'{moved["black"]["before"]} -> {moved["black"]["after"]}'))
        results.append(("both seats are told",
                        await _await_msg(guest, {protocol.RATING_UPDATE}, 5) is not None,
                        ""))
        results.append(("rated at the standard tempo", moved["tempo"] == "rapid",
                        moved["tempo"]))
        await guest.close()
    return results


async def run_anonymous(url: str) -> list[tuple[str, bool, str]]:
    """A game between two players with no accounts is stored like any other,
    announced to both of them, and can be fetched back for a replay without
    anybody signing in.

    This is the whole of anonymous play: the row has null user ids, so nothing
    lists it and the browser that played it is the only thing that remembers
    the id - but the game itself is kept, in full, like every other one.
    """
    results = []
    async with _connect(url) as host:
        await host.send(json.dumps({"type": protocol.CREATE_ROOM, "params": {}}))
        created = await _await_msg(host, {protocol.ROOM_CREATED}, 5)
        if created is None:
            results.append(("anonymous room created", False, "no ROOM_CREATED"))
            return results
        guest = await _connect(url)
        await guest.send(json.dumps({"type": protocol.JOIN_ROOM,
                                     "code": created["code"]}))
        await _await_msg(guest, {protocol.ROOM_JOINED}, 5)
        await _ready(host)
        await _ready(guest)
        results.append(("anonymous game starts",
                        await _await_msg(host, {protocol.GAME_STATE}, 10) is not None,
                        ""))

        await guest.send(json.dumps({"type": protocol.RESIGN}))

        saved = await _await_msg(host, {protocol.GAME_SAVED}, 20)
        results.append(("an anonymous game is stored",
                        saved is not None and bool(saved.get("game_id")),
                        str(saved.get("game_id") if saved else "no GAME_SAVED")))
        results.append(("both anonymous seats are told",
                        await _await_msg(guest, {protocol.GAME_SAVED}, 10) is not None,
                        ""))

        if saved is not None:
            # The point of the whole scenario: no account was involved at any
            # stage, and the recording still comes back. Before this, the
            # client fell back to the frames it had been sent, which the
            # room's visibility settings had already stripped.
            await host.send(json.dumps({"type": protocol.GET_GAME,
                                        "id": saved["game_id"]}))
            record = await _await_msg(host, {protocol.GAME_RECORD, protocol.ERROR}, 10)
            ok = record is not None and record.get("type") == protocol.GAME_RECORD
            results.append(("a signed-out client may fetch it", ok,
                            str(record.get("reason") if record else "no reply")))
            header = ((record or {}).get("recording") or {}).get("header") or {}
            results.append(("it holds both sides",
                            set(header.get("mana", {})) == {"white", "black"},
                            f"mana={sorted(header.get('mana', {}))}"))
        await guest.close()
    return results


async def run_unrated(url: str) -> list[tuple[str, bool, str]]:
    """A custom tempo must not move a rating, however signed in both sides are."""
    results = []
    tag = secrets.token_hex(3)
    async with _connect(url) as host:
        await host.send(json.dumps({"type": protocol.SIGN_UP, "name": f"u{tag}w",
                                    "password": "a test password"}))
        who = await _await_msg(host, {protocol.AUTH_STATE, protocol.AUTH_ERROR}, 10)
        if who is None or who.get("type") == protocol.AUTH_ERROR:
            results.append(("accounts available", False,
                            (who or {}).get("reason", "no reply")))
            return results
        guest = await _connect(url)
        await guest.send(json.dumps({"type": protocol.SIGN_UP, "name": f"u{tag}b",
                                     "password": "a test password"}))
        await _await_msg(guest, {protocol.AUTH_STATE}, 10)

        # Default params are not one of the three presets.
        await host.send(json.dumps({"type": protocol.CREATE_ROOM, "params": {}}))
        created = await _await_msg(host, {protocol.ROOM_CREATED}, 5)
        await guest.send(json.dumps({"type": protocol.JOIN_ROOM,
                                     "code": created["code"]}))
        await _await_msg(guest, {protocol.ROOM_JOINED}, 5)
        await _ready(host)
        await _ready(guest)
        await _await_msg(host, {protocol.GAME_STATE}, 10)
        await guest.send(json.dumps({"type": protocol.RESIGN}))
        late = await _await_msg(host, {protocol.RATING_UPDATE}, 6)
        results.append(("a custom tempo moves no rating", late is None,
                        "none arrived" if late is None else "RATING_UPDATE sent"))
        await guest.close()
    return results


async def run_hostile(url: str) -> list[tuple[str, bool, str]]:
    results = []

    async def expect_error(label: str, msg: dict) -> None:
        async with _connect(url) as ws:
            await ws.send(json.dumps(msg))
            # The server greets every connection with SERVER_HELLO, so read past
            # it rather than treating the first frame as the answer.
            reply = await _await_msg(ws, {protocol.ERROR}, 3)
            results.append((label, reply is not None,
                            reply.get("reason", "?") if reply else "no reply"))

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
        reply = await _await_msg(ws, {protocol.ERROR}, 3)
        results.append(("malformed json", reply is not None,
                        reply.get("reason", "?") if reply else "no reply"))

    # NaN and Infinity are not JSON, but Python's parser reads them happily and
    # nothing downstream survives one: NaN passes every bounds check by failing
    # every comparison it is made of.
    async with _connect(url) as ws:
        await ws.send('{"type": "CREATE_ROOM", "params": {"movement_speed": NaN}}')
        reply = await _await_msg(ws, {protocol.ERROR}, 3)
        results.append(("nan literal refused",
                        reply is not None and reply.get("reason") == "malformed json",
                        reply.get("reason", "?") if reply else "no reply"))

    # A socket already seated in a room must not be able to claim a seat in
    # another one: the room it walks out of never learns it has gone.
    async with _connect(url) as ws:
        await ws.send(json.dumps({"type": protocol.CREATE_ROOM, "params": {}}))
        await _await_msg(ws, {protocol.ROOM_CREATED}, 3)
        await ws.send(json.dumps({"type": protocol.REJOIN, "code": "ZZZZ", "token": "x"}))
        reply = await _await_msg(ws, {protocol.ERROR}, 3)
        results.append(("rejoin while seated",
                        reply is not None and reply.get("reason") == "already in a room",
                        reply.get("reason", "?") if reply else "no reply"))

    # Flood: the server should close the socket rather than fall over. Poll for
    # the close instead of assuming one fixed delay, which a WAN round trip and
    # the server's own close handling can easily exceed.
    try:
        async with _connect(url) as ws:
            for _ in range(200):
                await ws.send(json.dumps({"type": protocol.PING, "t": 0}))
            deadline = asyncio.get_event_loop().time() + 8
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.25)
                await ws.send(json.dumps({"type": protocol.PING, "t": 0}))
            results.append(("flood closed", False, "still open after 8s"))
    except websockets.exceptions.ConnectionClosed:
        results.append(("flood closed", True, "connection closed"))

    return results


async def amain() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--origin", default=None,
                    help="Origin header to send; required against a server "
                         "that restricts origins")
    ap.add_argument("--pair", action="store_true")
    ap.add_argument("--hostile", action="store_true")
    ap.add_argument("--disconnect", action="store_true")
    ap.add_argument("--rejoin", action="store_true")
    ap.add_argument("--seat", action="store_true",
                    help="a seat left empty in the lobby is given back")
    ap.add_argument("--rematch", action="store_true")
    ap.add_argument("--accounts", action="store_true",
                    help="sign-up, a rated game, and the rating that follows; "
                         "needs a server with a database")
    ap.add_argument("--grace", type=float, default=3.0,
                    help="must match the server's --grace")
    ap.add_argument("--rooms", type=int, default=1)
    ap.add_argument("--duration", type=float, default=8.0)
    args = ap.parse_args()

    global ORIGIN
    ORIGIN = args.origin

    failed = False

    if args.pair:
        results = await asyncio.gather(
            *(run_pair(args.url, args.duration, i) for i in range(args.rooms))
        )
        codes = [r.get("code") for r in results]
        for r in results:
            status = "PASS" if r["ok"] else "FAIL"
            failed |= not r["ok"]
            if not r["ok"] and "why" in r:
                print(f"[{status}] {r['why']}")
                continue
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
                            (args.rejoin, run_rejoin),
                            (args.seat, run_lobby_seat)):
        if enabled:
            for label, ok, detail in await runner(args.url, args.grace):
                failed |= not ok
                print(f"[{'PASS' if ok else 'FAIL'}] {label}: {detail}")

    for enabled, runner in ((args.hostile, run_hostile),
                            (args.rematch, run_rematch),
                            (args.accounts, run_accounts),
                            (args.accounts, run_anonymous),
                            (args.accounts, run_unrated)):
        if enabled:
            for label, ok, detail in await runner(args.url):
                failed |= not ok
                print(f"[{'PASS' if ok else 'FAIL'}] {label}: {detail}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
