"""
WebSocket server entry point.

Hosts many concurrent games in one process. Run from the project root:

    python -m server.main [--port PORT] [--origin https://example.com]

Clients create or join rooms over the lobby protocol; see shared/protocol.py.
"""

import argparse
import asyncio
import http
import json
import logging
import math
import os
import time

import websockets
from websockets.asyncio.server import serve

from . import civs, params, room as room_mod
from .room import Connection, RoomManager, FINISHED, RUNNING
from shared import protocol

log = logging.getLogger("server")

MAX_CONN_PER_IP = 5
MAX_MSG_PER_SEC = 40
MAX_MESSAGE_BYTES = 4096
MAX_CONNECTIONS = 200       # process-wide; Fly's proxy caps lower, this is the floor
MAX_JOIN_FAILS = 10         # wrong room codes before the socket is closed


def _refuse_constant(name: str) -> float:
    """Reject NaN and Infinity at the parser.

    Python's json accepts all three as bare literals, which no browser will
    ever send and which nothing downstream is written to survive: NaN passes
    every bounds check by failing every comparison, and once one reaches the
    game state the snapshot it lands in is no longer valid JSON for anybody.
    """
    raise ValueError(f"non-finite number: {name}")


class Hub:
    def __init__(self) -> None:
        self.rooms = RoomManager()
        self.conns: set[Connection] = set()

    def conns_from(self, ip: str) -> int:
        return sum(1 for c in self.conns if c.ip == ip)

    # -- message handlers ---------------------------------------------------

    async def on_create(self, conn: Connection, msg: dict) -> None:
        if conn.room is not None:
            await conn.error("already in a room")
            return
        room = await self.rooms.create(conn, msg)
        if room is None:
            return
        await conn.send({
            "type":  protocol.ROOM_CREATED,
            "code":  room.code,
            "color": conn.color,
            "token": conn.token,
            "solo":  room.solo,
        })
        if room.solo:
            room.start_if_ready()
        else:
            await room.notify_state()

    async def on_join(self, conn: Connection, msg: dict) -> None:
        if conn.room is not None:
            await conn.error("already in a room")
            return
        code = str(msg.get("code", "")).strip().upper()
        room = await self.rooms.join(conn, code)
        if room is None:
            # A room code is four characters, and the reply says which of "no
            # such room", "full" and "already started" it was - enough to sweep
            # the space and walk into someone's private lobby. Ten wrong codes
            # and this socket is done; another attempt costs a new handshake,
            # and MAX_CONN_PER_IP bounds how many of those can be in flight.
            conn.join_fails += 1
            if conn.join_fails >= MAX_JOIN_FAILS:
                log.warning("closing %s after %d failed joins", conn.ip, conn.join_fails)
                asyncio.create_task(conn.ws.close(1008, "too many failed joins"))
            return
        conn.join_fails = 0     # a code that worked; this player is not sweeping
        await conn.send({
            "type":  protocol.ROOM_JOINED,
            "code":  room.code,
            "color": conn.color,
            "token": conn.token,
        })
        await room.notify_state()
        room.start_if_ready()

    async def on_quick_match(self, conn: Connection, msg: dict) -> None:
        if conn.room is not None:
            await conn.error("already in a room")
            return
        room = await self.rooms.quick_match(conn, msg)
        if room is None:
            return
        await conn.send({
            "type":  protocol.ROOM_JOINED,
            "code":  room.code,
            "color": conn.color,
            "token": conn.token,
        })
        await room.notify_state()
        room.start_if_ready()

    async def on_rejoin(self, conn: Connection, msg: dict) -> None:
        # Same guard the other three entry points have. Without it one socket
        # could sit in two rooms at once: conn.room names only the second, so
        # the first never hears about the disconnect, never starts its grace
        # forfeit, and keeps its game loop running against nobody.
        if conn.room is not None:
            await conn.error("already in a room")
            return
        code  = str(msg.get("code", "")).strip().upper()
        token = str(msg.get("token", ""))
        color = self.rooms.find_rejoin(code, token)
        if color is None:
            await conn.error("cannot rejoin")
            return
        room = self.rooms.rooms[code]
        conn.token = token
        await room.rejoin(conn, color)
        await conn.send({
            "type":  protocol.ROOM_JOINED,
            "code":  room.code,
            "color": color,
            "token": token,
        })
        log.info("room %s: %s rejoined", code, color)

    async def on_set_ready(self, conn: Connection, msg: dict) -> None:
        room = conn.room
        if room is None or room.state != room_mod.LOBBY or conn.color is None:
            return
        # A seat picks a civilization by name, and nothing else. Any `params`
        # in this message is ignored: the numbers are derived from the room's
        # tempo on the server (civs.resolve), because a client trusted to send
        # its own cooldown will send a zero. Checking the name against the
        # table is also what keeps it out of the opponent's DOM.
        civ = msg.get("civ")
        if civ is not None and civ not in civs.CIV_NAMES:
            log.warning("rejected civ from %s: %r", conn.ip, civ)
            await conn.error("unknown civilization")
            return
        # A civilization multiplies the tempo, so this is where a piece-size
        # modifier lands: the opening position has to be checked here too.
        _, reason = civs.resolve_checked(room.base_params, civ)
        if reason:
            log.warning("rejected ready from %s: %s", conn.ip, reason)
            await conn.error(reason)
            return
        # One client holds both seats in solo, so it says which one it is
        # readying. Anywhere else the seat is the one the server assigned, and
        # a solo client that names no seat readies both with the same choice.
        seats = [conn.color]
        if room.solo:
            named = msg.get("color")
            seats = [named] if named in ("white", "black") else ["white", "black"]
        for seat in seats:
            room.set_ready(seat, bool(msg.get("ready")), civ)
        await room.notify_state()
        room.start_if_ready()

    async def on_queue_move(self, conn: Connection, msg: dict) -> None:
        room = conn.room
        if room is None or room.state != RUNNING:
            return
        if not conn.allow_move():
            return                      # silently drop; a real client can't hit this
        dest = msg.get("destination") or [0.0, 0.0]
        try:
            dx, dy = float(dest[0]), float(dest[1])
        except (TypeError, ValueError, IndexError):
            await conn.error("bad destination")
            return
        # NaN passes every comparison a rule is written as: the distance check,
        # the direction sector, and "can you afford it". One such move used to
        # subtract NaN from the mana pool, after which no move ever cost
        # anything again. Nothing downstream is written to survive it.
        if not (math.isfinite(dx) and math.isfinite(dy)):
            log.warning("non-finite destination from %s", conn.ip)
            await conn.error("bad destination")
            return
        rejection = room.game.queue_move(str(msg.get("piece_id", "")), (dx, dy),
                                         conn.color or "white")
        if rejection:
            await conn.send(rejection)

    async def on_resign(self, conn: Connection, msg: dict) -> None:
        """Give up the game in progress. The seat stays connected, so the
        result arrives as a normal GAME_OVER rather than a disconnect."""
        room = conn.room
        if room is None or room.state != RUNNING or room.game is None:
            return
        if room.game.game_over:
            return
        # In solo both seats are this client, so it says which one gives up.
        color = conn.color or "white"
        if room.solo and msg.get("color") in ("white", "black"):
            color = msg["color"]
        log.info("room %s: %s resigns", room.code, color)
        room.game.forfeit(color)

    async def on_rematch(self, conn: Connection, msg: dict) -> None:
        """Play the same room again. The seats and the tempo stay; both sides
        pick a civilization and ready up through the ordinary path.

        Idempotent on purpose: both players will press it, and the second
        press arrives at a room already back in the lobby. Answering that with
        an error would put a failure on the screen of whoever was slower."""
        room = conn.room
        if room is None or conn.color is None or room.state == RUNNING:
            return
        if room.state == FINISHED:
            if not room.solo and any(room.clients[c] is None
                                     for c in ("white", "black")):
                await conn.error("your opponent has left")
                return
            log.info("room %s: rematch", room.code)
            room.reset_for_rematch()
        await room.notify_state()

    async def on_leave(self, conn: Connection, msg: dict) -> None:
        if conn.room is not None:
            await conn.room.on_disconnect(conn)
            self.rooms.sweep()
            conn.room = None
            conn.color = None

    async def dispatch(self, conn: Connection, msg: dict) -> None:
        kind = msg.get("type")
        if kind == protocol.QUEUE_MOVE:
            await self.on_queue_move(conn, msg)
        elif kind == protocol.CREATE_ROOM:
            await self.on_create(conn, msg)
        elif kind == protocol.JOIN_ROOM:
            await self.on_join(conn, msg)
        elif kind == protocol.QUICK_MATCH:
            await self.on_quick_match(conn, msg)
        elif kind == protocol.SET_READY:
            await self.on_set_ready(conn, msg)
        elif kind == protocol.REJOIN:
            await self.on_rejoin(conn, msg)
        elif kind == protocol.RESIGN:
            await self.on_resign(conn, msg)
        elif kind == protocol.LEAVE_ROOM:
            await self.on_leave(conn, msg)
        elif kind == protocol.REMATCH:
            await self.on_rematch(conn, msg)
        elif kind == protocol.PING:
            await conn.send({"type": protocol.PONG, "t": msg.get("t"),
                             "server_time": time.time()})
        else:
            await conn.error(f"unknown message type: {kind}")

    # -- connection lifecycle -----------------------------------------------

    async def handle(self, ws) -> None:
        ip = client_ip(ws)
        if len(self.conns) >= MAX_CONNECTIONS:
            log.warning("refusing connection from %s: server full", ip)
            await ws.close(1013, "server full")
            return
        if self.conns_from(ip) >= MAX_CONN_PER_IP:
            log.warning("refusing connection from %s: too many", ip)
            await ws.close(1008, "too many connections")
            return

        conn = Connection(ws, ip)
        self.conns.add(conn)
        await conn.send({"type": protocol.SERVER_HELLO, "version": protocol.VERSION})
        window_start, window_count = time.monotonic(), 0

        try:
            async for raw in ws:
                now = time.monotonic()
                if now - window_start >= 1.0:
                    window_start, window_count = now, 0
                window_count += 1
                if window_count > MAX_MSG_PER_SEC:
                    log.warning("flood from %s, closing", ip)
                    # close() waits for the peer's half of the handshake, which
                    # a flooding client never sends - that would pin this task
                    # and its connection slot for the full close timeout. Send
                    # the frame and drop the connection now instead.
                    asyncio.create_task(ws.close(1008, "message rate exceeded"))
                    break

                try:
                    msg = json.loads(raw, parse_constant=_refuse_constant)
                except (ValueError, TypeError):
                    await conn.error("malformed json")
                    continue
                if not isinstance(msg, dict):
                    await conn.error("message must be an object")
                    continue

                await self.dispatch(conn, msg)

        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception:
            log.exception("handler error for %s", ip)
        finally:
            self.conns.discard(conn)
            if conn.room is not None:
                await conn.room.on_disconnect(conn)
                self.rooms.sweep()


def client_ip(ws) -> str:
    """Real client address, honouring the proxy header set by Caddy/Fly.

    Never the first entry of X-Forwarded-For: a proxy *appends* to that header,
    so the first entry is whatever the client itself sent, and reading it let
    any non-browser client pick its own identity and walk past every per-IP
    limit here. The last entry is the one our own proxy wrote. Fly-Client-IP is
    set by Fly-Proxy and cannot be forged through it, so prefer that.
    """
    headers = ws.request.headers if ws.request else {}
    fly = headers.get("Fly-Client-IP")
    if fly:
        return fly.strip()
    fwd = headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[-1].strip()
    addr = ws.remote_address
    return addr[0] if addr else "?"


async def main() -> None:
    global MAX_CONN_PER_IP
    parser = argparse.ArgumentParser(description="Continuous Chess server")
    parser.add_argument("--host", default=params.SERVER_HOST)
    parser.add_argument("--port", type=int, default=params.SERVER_PORT)
    parser.add_argument("--origin", action="append", default=None,
                        help="allowed Origin; repeatable. Defaults to the "
                             "comma-separated ALLOWED_ORIGINS env var, which "
                             "lets the deployment set it without rebuilding "
                             "the image. Omit both to allow any origin "
                             "(local development only).")
    parser.add_argument("--grace", type=float, default=room_mod.DISCONNECT_GRACE,
                        help="seconds a disconnected player may be gone before "
                             "forfeiting")
    parser.add_argument("--max-conn-per-ip", type=int, default=MAX_CONN_PER_IP,
                        help="raise for local load testing, where every client "
                             "shares one address")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    MAX_CONN_PER_IP = args.max_conn_per_ip
    room_mod.DISCONNECT_GRACE = args.grace

    hub = Hub()

    def process_request(connection, request):
        if request.path == "/health":
            body = json.dumps(hub.rooms.stats() | {"connections": len(hub.conns)})
            return connection.respond(http.HTTPStatus.OK, body + "\n")
        return None

    origins = args.origin
    if not origins:
        env_origins = os.environ.get("ALLOWED_ORIGINS", "")
        origins = [o.strip() for o in env_origins.split(",") if o.strip()] or None
    if origins:
        log.info("restricting Origin to: %s", ", ".join(origins))
    else:
        log.warning("no --origin set: accepting connections from any origin")

    async with serve(hub.handle, args.host, args.port,
                     process_request=process_request,
                     origins=origins,
                     close_timeout=5,
                     max_size=MAX_MESSAGE_BYTES):
        log.info("listening on %s:%d", args.host, args.port)
        await hub.rooms.gc_loop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
