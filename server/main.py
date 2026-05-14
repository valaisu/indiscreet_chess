"""
WebSocket server entry point.

Run from the project root:
    python -m server.main [--solo] [--port PORT]

--solo   One client controls both colors (for local testing without a second window).
"""

import argparse
import asyncio
import json
import socket
import sys

import websockets
import websockets.exceptions

from . import params
from .game import GameState
from shared import protocol


class Server:
    def __init__(self, solo: bool,
                 params_white: dict | None = None,
                 params_black: dict | None = None) -> None:
        self.solo = solo
        self.game = GameState(solo=solo, params_white=params_white, params_black=params_black)
        # Maps color -> websocket. In solo mode both entries point to same ws.
        self.clients: dict[str, any] = {}
        self._ready = asyncio.Event()

    async def handle_client(self, ws) -> None:
        try:
            # --- Handshake: HELLO ---
            raw = await ws.recv()
            msg = json.loads(raw)
            if msg.get("type") != protocol.HELLO:
                await ws.close()
                return

            color = msg.get("player_id", "white")
            if color not in ("white", "black"):
                await ws.send(json.dumps({"type": protocol.ERROR,
                                          "reason": "player_id must be white or black"}))
                await ws.close()
                return

            if self.solo:
                self.clients["white"] = ws
                self.clients["black"] = ws
                print(f"[server] Solo client connected.")
                self._ready.set()
            else:
                if color in self.clients:
                    await ws.send(json.dumps({"type": protocol.ERROR,
                                              "reason": f"{color} slot already taken"}))
                    await ws.close()
                    return
                self.clients[color] = ws
                print(f"[server] {color} connected ({len(self.clients)}/2).")
                if len(self.clients) == 2:
                    self._ready.set()

            # --- Handshake: READY ---
            raw = await ws.recv()
            # Just consume it; game starts when _ready fires.

            # --- Main message loop ---
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == protocol.QUEUE_MOVE:
                    piece_id = msg.get("piece_id", "")
                    dest = msg.get("destination", [0.0, 0.0])
                    rejection = self.game.queue_move(piece_id, tuple(dest), color)
                    if rejection:
                        await ws.send(json.dumps(rejection))

        except websockets.exceptions.ConnectionClosed:
            print(f"[server] Client disconnected.")
        except Exception as exc:
            print(f"[server] Handler error: {exc}", file=sys.stderr)

    async def broadcast(self, state: dict) -> None:
        data = json.dumps(state)
        sent: set[int] = set()
        for ws in self.clients.values():
            ws_id = id(ws)
            if ws_id in sent:
                continue
            sent.add(ws_id)
            try:
                await ws.send(data)
            except Exception:
                pass

    async def run_game(self) -> None:
        print("[server] Waiting for players...")
        await self._ready.wait()
        print("[server] Starting game.")
        await self.game.run(self.broadcast)
        print(f"[server] Game over. Winner: {self.game.winner}")


class DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, ws_port: int, server: "Server") -> None:
        self._ws_port = ws_port
        self._server  = server
        self._transport = None

    def connection_made(self, transport) -> None:
        self._transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            msg = json.loads(data)
        except Exception:
            return
        if msg.get("type") != protocol.DISCOVER:
            return
        waiting = not self._server._ready.is_set()
        reply = json.dumps({
            "type":    protocol.ANNOUNCE,
            "port":    self._ws_port,
            "name":    socket.gethostname(),
            "waiting": waiting,
        }).encode()
        self._transport.sendto(reply, addr)


async def _run_discovery(ws_port: int, server: "Server") -> None:
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", protocol.DISCOVERY_PORT))
    sock.setblocking(False)
    await loop.create_datagram_endpoint(
        lambda: DiscoveryProtocol(ws_port, server),
        sock=sock,
    )
    print(f"[server] Discovery listening on UDP :{protocol.DISCOVERY_PORT}")
    await asyncio.Event().wait()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Indiscreet Chess server")
    parser.add_argument("--solo",        action="store_true")
    parser.add_argument("--host",        default=params.SERVER_HOST)
    parser.add_argument("--port",        type=int,   default=params.SERVER_PORT)
    # Shared params (apply to both players when no per-player override)
    parser.add_argument("--mana-refill", type=float, default=params.MANA_REFILL_RATE)
    parser.add_argument("--max-mana",    type=float, default=params.MAXIMUM_MANA)
    parser.add_argument("--base-cost",   type=float, default=params.BASE_MOVE_COST)
    parser.add_argument("--dist-cost",   type=float, default=params.DISTANCE_COST)
    parser.add_argument("--prep",        type=float, default=params.PREPARATION_PERIOD)
    parser.add_argument("--speed",       type=float, default=params.MOVEMENT_SPEED)
    parser.add_argument("--cooldown",    type=float, default=params.COOLDOWN)
    parser.add_argument("--freedom",     type=float, default=params.MOVEMENT_FREEDOM_DEG)
    parser.add_argument("--diameter",    type=float, default=params.DIAMETER_PIECE)
    # Per-player overrides (used in handicap mode)
    for _color in ("white", "black"):
        parser.add_argument(f"--{_color}-mana-refill", type=float, default=None)
        parser.add_argument(f"--{_color}-max-mana",    type=float, default=None)
        parser.add_argument(f"--{_color}-base-cost",   type=float, default=None)
        parser.add_argument(f"--{_color}-dist-cost",   type=float, default=None)
        parser.add_argument(f"--{_color}-prep",        type=float, default=None)
        parser.add_argument(f"--{_color}-speed",       type=float, default=None)
        parser.add_argument(f"--{_color}-cooldown",    type=float, default=None)
        parser.add_argument(f"--{_color}-freedom",     type=float, default=None)
        parser.add_argument(f"--{_color}-diameter",    type=float, default=None)
    args = parser.parse_args()

    def _make_pp(color: str) -> dict:
        def _g(attr: str, base: float) -> float:
            v = getattr(args, f"{color}_{attr}", None)
            return v if v is not None else base
        return {
            "mana_refill_rate":     _g("mana_refill", args.mana_refill),
            "maximum_mana":         _g("max_mana",    args.max_mana),
            "base_move_cost":       _g("base_cost",   args.base_cost),
            "distance_cost":        _g("dist_cost",   args.dist_cost),
            "preparation_period":   _g("prep",        args.prep),
            "movement_speed":       _g("speed",       args.speed),
            "cooldown":             _g("cooldown",    args.cooldown),
            "movement_freedom_deg": _g("freedom",     args.freedom),
            "diameter_piece":       _g("diameter",    args.diameter),
        }

    server = Server(solo=args.solo,
                    params_white=_make_pp("white"),
                    params_black=_make_pp("black"))
    print(f"[server] Listening on {args.host}:{args.port} "
          f"({'solo' if args.solo else 'multiplayer'})")

    async with websockets.serve(server.handle_client, args.host, args.port):
        async with asyncio.TaskGroup() as tg:
            tg.create_task(server.run_game())
            tg.create_task(_run_discovery(args.port, server))


if __name__ == "__main__":
    asyncio.run(main())
