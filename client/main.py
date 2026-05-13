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
    Renderer, WIN_W, WIN_H,
    draw_fullscreen_btn, fullscreen_btn_rect,
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

_CLICK_R_SELECT = 0.5   # forgiving radius used when nothing is selected
_CLICK_R_SWITCH = 0.3   # strict radius (≈ piece edge) used when a piece is already selected
_MOVE_SNAP_MAX  = 0.625   # max distance from click to a valid destination before move is ignored

_SQRT2 = math.sqrt(2.0)


def _snap_destination(bx: float, by: float, piece: dict,
                      freedom_deg: float) -> tuple[float, float]:
    """
    Return the nearest point in the piece's legal movement area to (bx, by).
    Legal area is a union of sectors: each direction has a cone of ±freedom_deg.
    If the click falls inside a sector, the exact click position is used (clamped
    to max distance). If outside all sectors, snaps to the nearest sector edge.
    """
    px, py = piece["x"], piece["y"]
    ptype  = piece["type"]
    owner  = piece["owner"]

    click_dx = bx - px
    click_dy = by - py
    click_r  = math.hypot(click_dx, click_dy)
    if click_r < 1e-9:
        return (px, py)
    nx, ny      = click_dx / click_r, click_dy / click_r
    click_angle = math.atan2(click_dy, click_dx)
    freedom_rad = math.radians(freedom_deg)

    def board_max(dx: float, dy: float) -> float:
        t = float("inf")
        if dx > 1e-9:    t = min(t, (8.0 - px) / dx)
        elif dx < -1e-9: t = min(t, px / -dx)
        if dy > 1e-9:    t = min(t, (8.0 - py) / dy)
        elif dy < -1e-9: t = min(t, py / -dy)
        return max(0.0, t)

    best_pt: tuple[float, float] = (bx, by)
    best_d  = float("inf")

    def try_sector(dx: float, dy: float, max_t: float) -> None:
        nonlocal best_pt, best_d
        if max_t <= 0:
            return
        center_angle = math.atan2(dy, dx)
        delta = (click_angle - center_angle + math.pi) % (2 * math.pi) - math.pi

        if abs(delta) <= freedom_rad:
            # Inside sector: use exact click direction, clamp to max distance.
            actual_max = min(max_t, board_max(nx, ny))
            r  = min(click_r, actual_max)
            cx, cy = px + r * nx, py + r * ny
            d  = click_r - r  # >= 0 since r <= click_r
        else:
            # Outside sector: snap to the nearest edge ray.
            edge_angle = center_angle + math.copysign(freedom_rad, delta)
            ex, ey = math.cos(edge_angle), math.sin(edge_angle)
            edge_max = min(max_t, board_max(ex, ey))
            t  = max(0.0, min(click_dx * ex + click_dy * ey, edge_max))
            cx, cy = px + t * ex, py + t * ey
            d  = math.hypot(bx - cx, by - cy)

        if d < best_d:
            best_d, best_pt = d, (cx, cy)

    if ptype == "knight":
        for adx, ady in [(2,1),(2,-1),(-2,1),(-2,-1),(1,2),(1,-2),(-1,2),(-1,-2)]:
            x, y = px + adx, py + ady
            if 0.0 < x < 8.0 and 0.0 < y < 8.0:
                d = math.hypot(bx - x, by - y)
                if d < best_d:
                    best_d, best_pt = d, (x, y)

    elif ptype == "pawn":
        fwd     = -1.0 if owner == "white" else 1.0
        max_fwd = 1.0 if piece.get("has_moved") else 2.0
        try_sector(0.0, fwd, min(max_fwd, board_max(0.0, fwd)))
        d_unit = 1.0 / _SQRT2
        for xdir in (-1.0, 1.0):
            dx, dy = xdir * d_unit, fwd * d_unit
            try_sector(dx, dy, min(_SQRT2, board_max(dx, dy)))

    elif ptype == "king":
        hor_max = 2.0 if not piece.get("has_moved") else 1.0
        for dx in (1.0, -1.0):
            try_sector(dx, 0.0, min(hor_max, board_max(dx, 0.0)))
        for dy in (1.0, -1.0):
            try_sector(0.0, dy, min(1.0, board_max(0.0, dy)))
        d_unit = 1.0 / _SQRT2
        for sdx, sdy in [(1,1),(1,-1),(-1,1),(-1,-1)]:
            try_sector(sdx * d_unit, sdy * d_unit,
                       min(_SQRT2, board_max(sdx * d_unit, sdy * d_unit)))

    else:  # rook, bishop, queen
        d_unit = 1.0 / _SQRT2
        ortho = [(1.0,0.0),(-1.0,0.0),(0.0,1.0),(0.0,-1.0)]
        diag  = [(d_unit,d_unit),(d_unit,-d_unit),(-d_unit,d_unit),(-d_unit,-d_unit)]
        if ptype == "rook":     dirs = ortho
        elif ptype == "bishop": dirs = diag
        else:                   dirs = ortho + diag
        for dx, dy in dirs:
            try_sector(dx, dy, board_max(dx, dy))

    return best_pt, best_d


def _find_piece_at(bx: float, by: float,
                   pieces: list[dict], radius: float) -> dict | None:
    best, best_d = None, radius
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
                  solo: bool,
                  renderer: Renderer) -> str | None:
    bx, by = renderer.px_to_board(*mouse_pos)
    if not (0 <= bx < 8 and 0 <= by < 8):
        return selected_id

    pieces = state["pieces"]

    if selected_id is None:
        clicked = _find_piece_at(bx, by, pieces, _CLICK_R_SELECT)
        if clicked:
            mine = solo or clicked["owner"] == player_color
            if mine and clicked["state"] == "idle":
                return clicked["id"]

    else:
        # Strict hit-test: auto-switching is off; only an exact click on a piece counts.
        clicked = _find_piece_at(bx, by, pieces, _CLICK_R_SWITCH)

        if clicked and clicked["id"] == selected_id:
            return None  # clicked own piece → deselect

        sel = next((p for p in pieces if p["id"] == selected_id), None)
        if clicked and clicked["state"] == "idle" and sel:
            if clicked["owner"] == sel["owner"]:
                return clicked["id"]  # precise click on friendly piece → switch

        if sel:
            freedom = state.get("freedom_deg", 5.0)
            (dest_x, dest_y), snap_d = _snap_destination(bx, by, sel, freedom)
            if snap_d > _MOVE_SNAP_MAX:
                return selected_id  # click too far from any valid destination
            send_q.put({
                "type": QUEUE_MOVE,
                "piece_id": selected_id,
                "destination": [dest_x, dest_y],
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

    renderer        = Renderer(player_color=None if solo else player_color,
                               display=config.get("display", {}))
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
                if event.key == pygame.K_F11:
                    pygame.display.toggle_fullscreen()
                elif event.key == pygame.K_f:
                    renderer.toggle_flip()
                elif event.key == pygame.K_ESCAPE:
                    if last_state and last_state.get("game_over"):
                        return
                    elif selected_id is not None:
                        selected_id = None
                    else:
                        return

            if event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    w, h = screen.get_size()
                    if fullscreen_btn_rect(w, h).collidepoint(event.pos):
                        pygame.display.toggle_fullscreen()
                    elif last_state and not last_state.get("game_over"):
                        selected_id = _handle_click(
                            event.pos, last_state, selected_id,
                            send_q, player_color, solo, renderer,
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

        mx, my = pygame.mouse.get_pos()
        draw_fullscreen_btn(screen, bool(screen.get_flags() & pygame.FULLSCREEN), mx, my)
        pygame.display.flip()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    pygame.init()
    screen = pygame.display.set_mode((WIN_W, WIN_H), pygame.RESIZABLE)
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
