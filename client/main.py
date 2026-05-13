"""
Indiscreet Chess — client entry point.

Run from project root:
    python -m client.main

The start menu handles mode selection (Solo / Host / Join) and parameters.
"""

import asyncio
import json
import math
import queue
import subprocess
import sys
import threading
import time

import pygame

from client.menu import run_menu
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
# Server spawning
# ---------------------------------------------------------------------------

def _spawn_server(config: dict) -> subprocess.Popen:
    p = config["params"]
    args = [
        sys.executable, "-m", "server.main",
        "--port",        str(config["port"]),
        "--mana-refill", str(p["mana_refill_rate"]),
        "--max-mana",    str(p["maximum_mana"]),
        "--base-cost",   str(p["base_move_cost"]),
        "--dist-cost",   str(p["distance_cost"]),
        "--prep",        str(p["preparation_period"]),
        "--speed",       str(p["movement_speed"]),
        "--cooldown",    str(p["cooldown"]),
        "--freedom",     str(p["movement_freedom_deg"]),
        "--diameter",    str(p["diameter_piece"]),
    ]
    if config["mode"] == "solo":
        args.append("--solo")
    return subprocess.Popen(args)


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
        if clicked:
            mine = solo or clicked["owner"] == player_color
            if mine and clicked["state"] == "idle":
                return clicked["id"]

    else:
        if clicked and clicked["id"] == selected_id:
            return None  # deselect

        sel = next((p for p in pieces if p["id"] == selected_id), None)
        if clicked and clicked["state"] == "idle" and sel:
            if clicked["owner"] == sel["owner"]:
                return clicked["id"]

        send_q.put({
            "type": QUEUE_MOVE,
            "piece_id": selected_id,
            "destination": [round(bx, 3), round(by, 3)],
        })
        return None

    return selected_id


# ---------------------------------------------------------------------------
# Game loop
# ---------------------------------------------------------------------------

def _game_loop(screen: pygame.Surface, config: dict) -> None:
    solo         = config["mode"] == "solo"
    player_color = "white" if config["mode"] != "join" else "black"
    url          = f"ws://{config['host_ip']}:{config['port']}"

    recv_q: queue.Queue = queue.Queue()
    send_q: queue.Queue = queue.Queue()

    net_thread = threading.Thread(
        target=_run_network,
        args=(url, player_color, recv_q, send_q),
        daemon=True,
    )
    net_thread.start()

    pygame.display.set_caption("Indiscreet Chess")
    clock = pygame.time.Clock()

    renderer        = Renderer(player_color=None if solo else player_color)
    last_state: dict | None = None
    last_state_time: float  = 0.0
    selected_id: str | None = None

    while True:
        clock.tick(60)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if last_state and last_state.get("game_over"):
                        return
                    elif selected_id is not None:
                        selected_id = None
                    else:
                        return

            if event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1 and last_state and not last_state.get("game_over"):
                    selected_id = _handle_click(
                        event.pos, last_state, selected_id,
                        send_q, player_color, solo,
                    )
                if event.button == 3:
                    selected_id = None

        try:
            while True:
                msg = recv_q.get_nowait()
                if "_error" in msg:
                    pass
                elif msg.get("type") == GAME_STATE:
                    last_state      = msg
                    last_state_time = time.monotonic()
                elif msg.get("type") == MOVE_REJECTED:
                    print(f"[client] Rejected: {msg.get('piece_id')} — {msg.get('reason')}")
        except queue.Empty:
            pass

        if last_state:
            elapsed = time.monotonic() - last_state_time
            interp  = interpolate(last_state, elapsed)
            renderer.render(screen, interp, selected_id)
        else:
            renderer.render_waiting(screen)

        pygame.display.flip()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    pygame.init()
    screen = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption("Indiscreet Chess")

    while True:
        config = run_menu(screen)

        server_proc = None
        if config["mode"] in ("solo", "host"):
            server_proc = _spawn_server(config)
            time.sleep(0.5)   # give server time to bind

        try:
            _game_loop(screen, config)
        finally:
            if server_proc:
                server_proc.terminate()
                server_proc.wait()


if __name__ == "__main__":
    main()
