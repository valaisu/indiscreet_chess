"""
WebSocket server entry point.

Run from the project root:
    python -m server.main [--solo] [--port PORT]

--solo   One client controls both colors (for local testing without a second window).
"""

import argparse
import asyncio
import json
import sys

import websockets
import websockets.exceptions

from . import params
from .game import GameState
from shared import protocol


class Server:
    def __init__(self, solo: bool) -> None:
        self.solo = solo
        self.game = GameState(solo=solo)
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


async def main() -> None:
    parser = argparse.ArgumentParser(description="Indiscreet Chess server")
    parser.add_argument("--solo",        action="store_true")
    parser.add_argument("--host",        default=params.SERVER_HOST)
    parser.add_argument("--port",        type=int,   default=params.SERVER_PORT)
    parser.add_argument("--mana-refill", type=float, default=params.MANA_REFILL_RATE)
    parser.add_argument("--max-mana",    type=float, default=params.MAXIMUM_MANA)
    parser.add_argument("--base-cost",   type=float, default=params.BASE_MOVE_COST)
    parser.add_argument("--dist-cost",   type=float, default=params.DISTANCE_COST)
    parser.add_argument("--prep",        type=float, default=params.PREPARATION_PERIOD)
    parser.add_argument("--speed",       type=float, default=params.MOVEMENT_SPEED)
    parser.add_argument("--cooldown",    type=float, default=params.COOLDOWN)
    parser.add_argument("--freedom",     type=float, default=params.MOVEMENT_FREEDOM_DEG)
    parser.add_argument("--diameter",    type=float, default=params.DIAMETER_PIECE)
    args = parser.parse_args()

    params.MANA_REFILL_RATE      = args.mana_refill
    params.MAXIMUM_MANA          = args.max_mana
    params.BASE_MOVE_COST        = args.base_cost
    params.DISTANCE_COST         = args.dist_cost
    params.PREPARATION_PERIOD    = args.prep
    params.MOVEMENT_SPEED        = args.speed
    params.COOLDOWN              = args.cooldown
    params.MOVEMENT_FREEDOM_DEG  = args.freedom
    params.DIAMETER_PIECE        = args.diameter

    server = Server(solo=args.solo)
    print(f"[server] Listening on {args.host}:{args.port} "
          f"({'solo' if args.solo else 'multiplayer'})")

    async with websockets.serve(server.handle_client, args.host, args.port):
        await server.run_game()


if __name__ == "__main__":
    asyncio.run(main())
