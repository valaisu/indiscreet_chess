"""
Room layer: many concurrent games in one server process.

A Room owns a GameState and the task driving it. GameState.run() already takes
a broadcast callback, so the game loop itself is unaware that rooms exist.
"""

import asyncio
import json
import logging
import secrets
import time

from . import civs, params
from .game import GameState
from .pieces import start_overlap_reason
from shared import protocol

log = logging.getLogger("room")

LOBBY, RUNNING, FINISHED = "lobby", "running", "finished"

DISCONNECT_GRACE = 30.0     # seconds a player may be gone before forfeiting
LOBBY_TTL        = 900.0    # unjoined rooms expire after 15 minutes
FINISHED_TTL     = 300.0    # finished rooms stay rematchable; a replay takes minutes
GC_INTERVAL      = 30.0

OPPOSITE = {"white": "black", "black": "white"}


def dump(msg: dict) -> str:
    """Serialise one frame. allow_nan=False because NaN and Infinity are not
    JSON: Python writes the bare tokens happily and every browser's JSON.parse
    then throws on the frame, which does not break one value, it breaks the
    socket for the rest of the game. Raising here turns a bad number into a
    dropped frame and a stack trace instead."""
    return json.dumps(msg, allow_nan=False)


class Connection:
    """One websocket. Owns its identity and rate-limit budget."""

    def __init__(self, ws, ip: str) -> None:
        self.ws = ws
        self.ip = ip
        self.room: "Room | None" = None
        self.color: str | None = None
        self.token: str = ""
        # Token bucket for QUEUE_MOVE. Legitimate play is mana-bound to roughly
        # 5 instant moves then ~0.3/s, so this is generous.
        self.move_tokens: float = 10.0
        self.move_tokens_at: float = time.monotonic()
        # Wrong room codes tried on this socket. Guessing them is how a private
        # room gets found, and a real player mistypes a code once or twice.
        self.join_fails: int = 0

    async def send(self, msg: dict) -> None:
        try:
            data = dump(msg)
        except ValueError:
            log.exception("refusing to send a non-finite value to %s", self.ip)
            return
        try:
            await self.ws.send(data)
        except Exception:
            pass

    async def error(self, reason: str) -> None:
        await self.send({"type": protocol.ERROR, "reason": reason})

    def allow_move(self) -> bool:
        now = time.monotonic()
        self.move_tokens = min(10.0, self.move_tokens + (now - self.move_tokens_at) * 3.0)
        self.move_tokens_at = now
        if self.move_tokens < 1.0:
            return False
        self.move_tokens -= 1.0
        return True


class Room:
    def __init__(self, code: str, base_params: dict,
                 public: bool = False, solo: bool = False,
                 view: dict | None = None) -> None:
        self.code = code
        self.public = public
        self.solo = solo
        # Fixed by whoever opened the room, like the tempo: both sides play
        # under the same information rules.
        self.view: dict = params.build_view(view)
        # The tempo the room was made with, and the only thing a client gets to
        # choose. It is announced in ROOM_STATE, so a joiner sees what they are
        # sitting down to before they ready up.
        self.base_params: dict = dict(base_params)
        # Filled in at Ready, by the server, from base_params and the seat's
        # civilization. Never taken from a client: a seat that names its own
        # numbers names its own physics.
        self.params: dict[str, dict] = {"white": dict(base_params),
                                        "black": dict(base_params)}
        self.game: GameState | None = None
        self.ready: dict[str, bool] = {"white": False, "black": False}
        self.civ: dict[str, str | None] = {"white": None, "black": None}
        self.clients: dict[str, Connection | None] = {"white": None, "black": None}
        self.tokens: dict[str, str] = {}
        self.state: str = LOBBY
        self.created_at = time.monotonic()
        self.finished_at: float | None = None
        self.task: asyncio.Task | None = None
        self._grace: dict[str, asyncio.Task] = {}

    # -- membership ---------------------------------------------------------

    def free_seat(self) -> str | None:
        for color in ("white", "black"):
            if self.clients[color] is None and color not in self.tokens:
                return color
        return None

    def occupants(self) -> int:
        return len({id(c) for c in self.clients.values() if c is not None})

    async def seat(self, conn: Connection, color: str) -> None:
        conn.room = self
        conn.color = color
        conn.token = secrets.token_urlsafe(12)
        self.clients[color] = conn
        self.tokens[color] = conn.token

    async def broadcast(self, msg: dict) -> None:
        try:
            data = dump(msg)
        except ValueError:
            log.exception("room %s: refusing to broadcast a non-finite value", self.code)
            return
        seen: set[int] = set()
        for conn in self.clients.values():
            if conn is None or id(conn) in seen:
                continue
            seen.add(id(conn))
            try:
                await conn.ws.send(data)
            except Exception:
                pass

    async def broadcast_game(self, game) -> None:
        """Game snapshots, one per seat: each side may be entitled to see
        different things. Solo holds both seats on one socket, so it gets the
        unfiltered view once."""
        seen: set[int] = set()
        for color, conn in self.clients.items():
            if conn is None or id(conn) in seen:
                continue
            seen.add(id(conn))
            await conn.send(game.to_dict(None if self.solo else color))

    async def notify_state(self) -> None:
        # Deliberately no civ names: the pick stays hidden until the game runs.
        await self.broadcast({
            "type":    protocol.ROOM_STATE,
            "code":    self.code,
            "players": self.occupants(),
            "waiting": self.state == LOBBY,
            "seated":  {c: self.clients[c] is not None for c in ("white", "black")},
            "base_params": self.base_params,
            "view": self.view,
            "ready":   dict(self.ready),
        })

    def set_ready(self, color: str, ready: bool, civ: str | None) -> None:
        """Record one seat's choice. A solo client owns both seats and readies
        them one at a time, so each can be a different civilization.

        The params follow from the civilization; the caller has already
        resolved and checked them (see civs.resolve_checked)."""
        self.ready[color] = ready
        self.civ[color] = civ
        if ready:
            self.params[color] = civs.resolve(self.base_params, civ)

    def reset_for_rematch(self) -> None:
        """Put a finished room back in the lobby with the same seats and the
        same tempo. Readiness and civilizations are cleared rather than
        carried over: picking again is most of the reason to want a rematch,
        and it puts the room back through the one path that starts a game."""
        self.game = None
        self.task = None
        self.state = LOBBY
        self.finished_at = None
        self.created_at = time.monotonic()   # LOBBY_TTL starts again
        self.ready = {"white": False, "black": False}
        self.civ = {"white": None, "black": None}
        self.params = {"white": dict(self.base_params),
                       "black": dict(self.base_params)}

    # -- lifecycle ----------------------------------------------------------

    def start_if_ready(self) -> None:
        if self.state != LOBBY:
            return
        if any(self.clients[c] is None for c in ("white", "black")):
            return
        if not all(self.ready[c] for c in ("white", "black")):
            return
        self.game = GameState(solo=self.solo,
                              params_white=self.params["white"],
                              params_black=self.params["black"],
                              civs=dict(self.civ),
                              view=self.view)
        self.state = RUNNING
        self.task = asyncio.create_task(self._run())
        log.info("room %s starting", self.code)

    async def _run(self) -> None:
        try:
            await self.game.run(self.broadcast_game)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("room %s crashed", self.code)
        finally:
            self.state = FINISHED
            self.finished_at = time.monotonic()
            log.info("room %s over, winner=%s", self.code, self.game.winner)

    async def on_disconnect(self, conn: Connection) -> None:
        color = conn.color
        if color is None or self.clients.get(color) is not conn:
            return
        # A solo client is seated twice. Clearing only the seat it was dealt
        # left the other one pointing at the dead socket, so the game kept
        # running and kept broadcasting to it.
        for seat, seated in list(self.clients.items()):
            if seated is conn:
                self.clients[seat] = None

        if self.solo:
            if (self.state == RUNNING and self.game is not None
                    and not self.game.game_over):
                # Nobody is left to play it out and nobody to tell.
                self.game.forfeit(color)
            return

        if self.state == RUNNING:
            await self.broadcast({
                "type": protocol.OPPONENT_LEFT,
                "color": color,
                "grace_seconds": DISCONNECT_GRACE,
            })
            self._grace[color] = asyncio.create_task(self._forfeit_after_grace(color))
        else:
            await self.notify_state()

    async def _forfeit_after_grace(self, color: str) -> None:
        try:
            await asyncio.sleep(DISCONNECT_GRACE)
        except asyncio.CancelledError:
            return
        if (self.clients.get(color) is None and self.state == RUNNING
                and self.game is not None and not self.game.game_over):
            log.info("room %s: %s forfeits (disconnect timeout)", self.code, color)
            self.game.forfeit(color)

    async def rejoin(self, conn: Connection, color: str) -> None:
        grace = self._grace.pop(color, None)
        if grace:
            grace.cancel()
        conn.room = self
        conn.color = color
        self.clients[color] = conn
        await self.broadcast({"type": protocol.OPPONENT_REJOINED, "color": color})

    def abandoned(self) -> bool:
        if self.state == LOBBY:
            return self.occupants() == 0 or time.monotonic() - self.created_at > LOBBY_TTL
        if self.state == FINISHED:
            # Empty goes at once: the long TTL exists so the players still
            # sitting there can agree a rematch, not to keep a husk alive.
            return (self.occupants() == 0
                    or (self.finished_at is not None
                        and time.monotonic() - self.finished_at > FINISHED_TTL))
        return False

    def shutdown(self) -> None:
        for t in self._grace.values():
            t.cancel()
        self._grace.clear()
        if self.task and not self.task.done():
            self.task.cancel()


class RoomManager:
    MAX_ROOMS_PER_IP = 3

    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    # -- creation / joining -------------------------------------------------

    def _new_code(self) -> str:
        while True:
            code = "".join(secrets.choice(protocol.CODE_ALPHABET)
                           for _ in range(protocol.CODE_LENGTH))
            if code not in self.rooms:
                return code

    def rooms_owned_by(self, ip: str) -> int:
        return sum(
            1 for r in self.rooms.values()
            if any(c is not None and c.ip == ip for c in r.clients.values())
        )

    async def create(self, conn: Connection, msg: dict, public: bool = False) -> Room | None:
        if self.rooms_owned_by(conn.ip) >= self.MAX_ROOMS_PER_IP:
            await conn.error("too many rooms from this address")
            return None

        # One tempo per room, applied to both seats. There used to be a
        # `handicap` branch here taking separate params for white and black:
        # only ROOM_STATE's base_params reaches the joiner, so a room could
        # cripple the seat you were about to sit in without ever showing you.
        # No shipped client sent it.
        base = msg.get("params")
        reason = params.validate_params(base) or start_overlap_reason(base)
        if reason:
            log.warning("rejected params from %s: %s", conn.ip, reason)
            await conn.error(reason)
            return None

        reason = params.validate_view(msg.get("view"))
        if reason:
            log.warning("rejected view from %s: %s", conn.ip, reason)
            await conn.error(reason)
            return None

        solo = bool(msg.get("solo"))
        room = Room(self._new_code(), base or {}, public=public and not solo,
                    solo=solo, view=msg.get("view"))
        self.rooms[room.code] = room
        await room.seat(conn, "white")
        if solo:
            room.clients["black"] = conn
            room.tokens["black"] = conn.token
        log.info("room %s created by %s (public=%s)", room.code, conn.ip, public)
        return room

    async def join(self, conn: Connection, code: str) -> Room | None:
        room = self.rooms.get(code)
        if room is None:
            await conn.error("no such room")
            return None
        if room.state != LOBBY:
            await conn.error("game already started")
            return None
        seat = room.free_seat()
        if seat is None:
            await conn.error("room is full")
            return None
        await room.seat(conn, seat)
        return room

    async def quick_match(self, conn: Connection, msg: dict) -> Room | None:
        for room in self.rooms.values():
            if room.public and room.state == LOBBY and room.free_seat():
                seat = room.free_seat()
                await room.seat(conn, seat)
                log.info("quick match: %s joined %s", conn.ip, room.code)
                return room
        return await self.create(conn, msg, public=True)

    def find_rejoin(self, code: str, token: str) -> str | None:
        """Return the colour this token owns in the room, if it is reclaimable."""
        room = self.rooms.get(code)
        if room is None:
            return None
        for color, tok in room.tokens.items():
            if secrets.compare_digest(tok, token) and room.clients[color] is None:
                return color
        return None

    # -- housekeeping -------------------------------------------------------

    def sweep(self) -> int:
        dead = [code for code, room in self.rooms.items() if room.abandoned()]
        for code in dead:
            self.rooms.pop(code).shutdown()
        if dead:
            log.info("gc removed %d room(s): %s", len(dead), ", ".join(dead))
        return len(dead)

    async def gc_loop(self) -> None:
        while True:
            await asyncio.sleep(GC_INTERVAL)
            try:
                self.sweep()
            except Exception:
                log.exception("gc sweep failed")

    def stats(self) -> dict:
        by_state = {LOBBY: 0, RUNNING: 0, FINISHED: 0}
        for room in self.rooms.values():
            by_state[room.state] = by_state.get(room.state, 0) + 1
        return {
            "rooms": len(self.rooms),
            "lobby": by_state[LOBBY],
            "running": by_state[RUNNING],
            "finished": by_state[FINISHED],
            "players": sum(r.occupants() for r in self.rooms.values()),
        }
