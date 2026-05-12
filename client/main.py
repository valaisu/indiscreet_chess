"""
Indiscreet Chess — client entry point.

Run from project root:
    python -m client.main [--host HOST] [--port PORT] [--color white|black] [--solo]

--solo    Control both colours (server must also run with --solo).
"""

import argparse
import asyncio
import json
import math
import queue
import sys
import threading
import time

import pygame

from client.renderer import (
    Renderer, board_to_px, px_to_board,
    PIECE_R, SQ, WIN_W, WIN_H,
)
from client.interpolator import interpolate
from shared.protocol import (
    HELLO, READY, QUEUE_MOVE,
    GAME_STATE, MOVE_REJECTED,
)

# ---------------------------------------------------------------------------
# Networking (asyncio in a background thread)
# ---------------------------------------------------------------------------

def _run_network(url: str, color: str,
                 recv_q: queue.Queue, send_q: queue.Queue) -> None:
    asyncio.run(_network_loop(url, color, recv_q, send_q))


async def _network_loop(url: str, color: str,
                        recv_q: queue.Queue, send_q: queue.Queue) -> None:
    import websockets

    print(f"[client] Connecting to {url} as {color}…")
    try:
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"type": HELLO, "player_id": color}))
            await ws.send(json.dumps({"type": READY}))
            print("[client] Connected.")

            async def receiver() -> None:
                async for raw in ws:
                    recv_q.put(json.loads(raw))

            async def sender() -> None:
                while True:
                    try:
                        msg = send_q.get_nowait()
                        await ws.send(json.dumps(msg))
                    except queue.Empty:
                        pass
                    await asyncio.sleep(0.008)

            await asyncio.gather(receiver(), sender())

    except Exception as exc:
        recv_q.put({"_error": str(exc)})
        print(f"[client] Network error: {exc}")


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

_CLICK_R_BOARD = (PIECE_R / SQ) + 0.05   # click radius in board units


def _find_piece_at(bx: float, by: float,
                   pieces: list[dict]) -> dict | None:
    best, best_d = None, _CLICK_R_BOARD
    for p in pieces:
        if p["type"] == "ghost":
            continue
        d = math.hypot(p["x"] - bx, p["y"] - by)
        if d < best_d:
            best_d = d
            best = p
    return best


def _handle_click(mouse_pos: tuple[int, int],
                  state: dict,
                  selected_id: str | None,
                  send_q: queue.Queue,
                  player_color: str,
                  solo: bool) -> str | None:
    bx, by = px_to_board(*mouse_pos)
    if not (0 <= bx < 8 and 0 <= by < 8):
        return selected_id

    pieces = state["pieces"]
    clicked = _find_piece_at(bx, by, pieces)

    if selected_id is None:
        # Nothing selected: try to select a piece.
        if clicked:
            mine = solo or clicked["owner"] == player_color
            if mine and clicked["state"] == "idle":
                return clicked["id"]

    else:
        # Something is already selected.
        if clicked and clicked["id"] == selected_id:
            return None  # click same piece → deselect

        # Click another own idle piece → switch selection.
        if clicked and clicked["state"] == "idle":
            mine = solo or clicked["owner"] == player_color
            if mine:
                return clicked["id"]

        # Otherwise: send move command to server.
        send_q.put({
            "type": QUEUE_MOVE,
            "piece_id": selected_id,
            "destination": [round(bx, 3), round(by, 3)],
        })
        return None

    return selected_id


# ---------------------------------------------------------------------------
# Main game loop
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Indiscreet Chess client")
    parser.add_argument("--host",  default="localhost")
    parser.add_argument("--port",  type=int, default=8765)
    parser.add_argument("--color", default="white", choices=["white", "black"])
    parser.add_argument("--solo",  action="store_true",
                        help="Control both colours (server must run --solo too)")
    args = parser.parse_args()

    recv_q: queue.Queue = queue.Queue()
    send_q: queue.Queue = queue.Queue()

    net_thread = threading.Thread(
        target=_run_network,
        args=(f"ws://{args.host}:{args.port}", args.color, recv_q, send_q),
        daemon=True,
    )
    net_thread.start()

    pygame.init()
    screen = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption("Indiscreet Chess")
    clock = pygame.time.Clock()

    player_color = args.color
    solo = args.solo

    renderer   = Renderer(player_color=None if solo else player_color)
    last_state: dict | None = None
    last_state_time: float  = 0.0
    selected_id: str | None = None

    while True:
        clock.tick(60)

        # --- Events ---
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if last_state and last_state.get("game_over"):
                        pygame.quit()
                        sys.exit()
                    selected_id = None

            if event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1 and last_state and not last_state.get("game_over"):
                    selected_id = _handle_click(
                        event.pos, last_state, selected_id,
                        send_q, player_color, solo,
                    )
                if event.button == 3:
                    selected_id = None  # right-click deselects

        # --- Drain network queue, keep latest state ---
        try:
            while True:
                msg = recv_q.get_nowait()
                if "_error" in msg:
                    pass  # already printed by network thread
                elif msg.get("type") == GAME_STATE:
                    last_state      = msg
                    last_state_time = time.monotonic()
                elif msg.get("type") == MOVE_REJECTED:
                    print(f"[client] Rejected: {msg.get('piece_id')} — {msg.get('reason')}")
        except queue.Empty:
            pass

        # --- Render ---
        if last_state:
            elapsed = time.monotonic() - last_state_time
            interp  = interpolate(last_state, elapsed)
            renderer.render(screen, interp, selected_id)
        else:
            renderer.render_waiting(screen)

        pygame.display.flip()


if __name__ == "__main__":
    main()
