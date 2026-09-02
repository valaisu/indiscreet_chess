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
import os
import time

import websockets
from websockets.asyncio.server import serve

from . import params, room as room_mod
from .pieces import start_overlap_reason
from .room import Connection, RoomManager, RUNNING
from shared import protocol

log = logging.getLogger("server")

MAX_CONN_PER_IP = 5
MAX_MSG_PER_SEC = 40
MAX_MESSAGE_BYTES = 4096


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
            return
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
        p = msg.get("params")
        # A civilization is applied client-side, so this is where a piece-size
        # modifier lands: the opening position has to be checked here too.
        reason = params.validate_params(p) or start_overlap_reason(p)
        if reason:
            log.warning("rejected ready params from %s: %s", conn.ip, reason)
            await conn.error(reason)
            return
        civ = msg.get("civ")
        if civ is not None and (not isinstance(civ, str) or len(civ) > 20):
            await conn.error("bad civ")
            return
        room.set_ready(conn.color, bool(msg.get("ready")), civ, p or {})
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
        rejection = room.game.queue_move(str(msg.get("piece_id", "")), (dx, dy),
                                         conn.color or "white")
        if rejection:
            await conn.send(rejection)

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
        elif kind == protocol.LEAVE_ROOM:
            await self.on_leave(conn, msg)
        elif kind == protocol.PING:
            await conn.send({"type": protocol.PONG, "t": msg.get("t"),
                             "server_time": time.time()})
        else:
            await conn.error(f"unknown message type: {kind}")

    # -- connection lifecycle -----------------------------------------------

    async def handle(self, ws) -> None:
        ip = client_ip(ws)
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
                    # a flooding client never sends — that would pin this task
                    # and its connection slot for the full close timeout. Send
                    # the frame and drop the connection now instead.
                    asyncio.create_task(ws.close(1008, "message rate exceeded"))
                    break

                try:
                    msg = json.loads(raw)
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
    """Real client address, honouring the proxy header set by Caddy/Fly."""
    fwd = ws.request.headers.get("X-Forwarded-For") if ws.request else None
    if fwd:
        return fwd.split(",")[0].strip()
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
