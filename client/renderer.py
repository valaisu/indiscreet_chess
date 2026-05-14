"""
All pygame drawing code. No game logic here.

Board coordinate system (matches server):
  (0, 0) = top-left (black back rank)
  (8, 8) = bottom-right (white back rank)
  Each square has side = SQUARE_SIDE board units = SQ pixels at base scale.
"""

import math
import os

import pygame

# ---------------------------------------------------------------------------
# Bundled font
# ---------------------------------------------------------------------------

_FONT_PATH = os.path.join(os.path.dirname(__file__), "assets", "DejaVuSans.ttf")


def _load_font(size: int) -> pygame.font.Font:
    try:
        return pygame.font.Font(_FONT_PATH, size)
    except (FileNotFoundError, OSError):
        return pygame.font.SysFont("dejavusans,arial,sans-serif", size)

# ---------------------------------------------------------------------------
# Base logical resolution  (800 × 840 reference window)
# ---------------------------------------------------------------------------

WIN_W = 800
WIN_H = 840

# Legacy module-level values — used by main.py to compute _CLICK_R_BOARD
# (which is scale-independent, expressed in board units)
SQ      = 80
PIECE_R = 24   # int(0.3 * SQ)

_BASE_BOARD_X = 80
_BASE_BOARD_Y = 100
_BASE_MANA_H  = 22

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

C_BG           = (30,  30,  30)
C_LIGHT        = (240, 217, 181)
C_DARK         = (181, 136,  99)
C_BOARD_BORDER = (100,  80,  60)

C_WHITE_FILL   = (255, 255, 255)
C_BLACK_FILL   = ( 22,  22,  22)
C_WHITE_BORDER = (200, 200, 200)   # ring drawn on black pieces
C_BLACK_BORDER = ( 45,  45,  45)   # ring drawn on white pieces
C_WHITE_ICON   = ( 12,  12,  12)   # icon text on white pieces
C_BLACK_ICON   = (245, 245, 245)   # icon text on black pieces

C_SELECT       = ( 80, 210,  80)
C_DEST_MARKER  = (220, 200,  50)
C_GHOST_FILL   = (160, 160, 200)

C_MANA_BG      = ( 25,  25,  55)
C_MANA_WHITE   = ( 70, 130, 200)
C_MANA_BLACK   = (180,  60,  60)

C_TEXT         = (220, 220, 220)
C_OVERLAY      = (  0,   0,   0, 160)
C_TIMER_PREP   = (220, 185,  50)   # gold  — preparation arc
C_TIMER_COOL   = ( 70, 160, 220)   # blue  — cooldown arc
C_WIN_TEXT     = (255, 220, 100)
C_HINT_OK      = (100, 210, 100,  80)  # affordable + legal
C_HINT_NO_MANA = (220, 140,  40,  80)  # legal direction, not enough mana
C_HINT_ILLEGAL = (180,  60,  60,  80)  # move not currently legal
C_SNAP_ZONE    = ( 70, 130, 220,  50)  # debug: expanded snap zone

# Mirrors server/params.py defaults — used for hint rendering
_BASE_MOVE_COST = 1.0
_DISTANCE_COST  = 0.2
_DIAMETER_PIECE = 0.6

_SQRT2 = math.sqrt(2.0)
_ORTHO = [(1, 0), (-1, 0), (0, 1), (0, -1)]


def _pval(data, owner: str, default):
    """Read a value that may be a float or a per-owner dict."""
    if isinstance(data, dict):
        return data.get(owner, default)
    return data if data is not None else default
_DIAG  = [( 1/_SQRT2,  1/_SQRT2), ( 1/_SQRT2, -1/_SQRT2),
          (-1/_SQRT2,  1/_SQRT2), (-1/_SQRT2, -1/_SQRT2)]
_ALL8  = _ORTHO + _DIAG


def _max_to_edge(bx: float, by: float, lx: float, ly: float) -> float:
    t = 8.0
    if lx > 1e-9:    t = min(t, (8.0 - bx) / lx)
    elif lx < -1e-9: t = min(t, bx / -lx)
    if ly > 1e-9:    t = min(t, (8.0 - by) / ly)
    elif ly < -1e-9: t = min(t, by / -ly)
    return max(0.0, t)


def _wedge(surf: pygame.Surface, cx: float, cy: float,
           angle: float, half: float, r: float,
           color: tuple, n: int = 12) -> None:
    if r < 1:
        return
    pts = [(cx, cy)]
    for i in range(n + 1):
        a = angle - half + 2 * half * i / n
        pts.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
    pygame.draw.polygon(surf, color, pts)


def _wedge_mana(surf: pygame.Surface, cx: float, cy: float,
                angle: float, half: float, full_r: float, mana_r: float) -> None:
    """Draw wedge colored green up to mana range, orange beyond."""
    if mana_r >= full_r:
        _wedge(surf, cx, cy, angle, half, full_r, C_HINT_OK)
    elif mana_r <= 0:
        _wedge(surf, cx, cy, angle, half, full_r, C_HINT_NO_MANA)
    else:
        _wedge(surf, cx, cy, angle, half, full_r, C_HINT_NO_MANA)
        _wedge(surf, cx, cy, angle, half, mana_r, C_HINT_OK)


def _snap_zone_pts(cx: float, cy: float, center_angle: float,
                   freedom_rad: float, max_t_px: float, snap_px: float,
                   n: int = 24) -> list[tuple[float, float]]:
    """Polygon for the Euclidean snap zone around one movement sector."""
    if max_t_px <= 0 or snap_px <= 0:
        return []
    half = freedom_rad + math.pi / 2
    pts = []
    for i in range(n + 1):
        diff = -half + 2 * half * i / n
        excess = max(0.0, abs(diff) - freedom_rad)
        if excess < 1e-9:
            r = max_t_px + snap_px
        elif excess >= math.pi / 2 - 1e-9:
            r = snap_px
        else:
            r = min(snap_px / math.sin(excess), max_t_px + snap_px)
        pts.append((cx + r * math.cos(center_angle + diff),
                    cy + r * math.sin(center_angle + diff)))
    pts.append((cx, cy))
    return pts


def _has_enemy_near(bx: float, by: float, owner: str,
                    lx: float, ly: float, max_dist: float,
                    pieces: list) -> bool:
    """True if an enemy piece lies within max_dist in the (lx, ly) direction."""
    check_angle = math.radians(20)
    for p in pieces:
        if p["owner"] == owner:
            continue
        dx, dy = p["x"] - bx, p["y"] - by
        dist = math.hypot(dx, dy)
        if dist < 1e-6 or dist > max_dist:
            continue
        dot = (dx * lx + dy * ly) / dist
        if math.acos(max(-1.0, min(1.0, dot))) <= check_angle:
            return True
    return False

# ---------------------------------------------------------------------------
# Piece labels
# ---------------------------------------------------------------------------

ICONS = {
    ("pawn",   "white"): "♙", ("pawn",   "black"): "♟",
    ("rook",   "white"): "♖", ("rook",   "black"): "♜",
    ("knight", "white"): "♘", ("knight", "black"): "♞",
    ("bishop", "white"): "♗", ("bishop", "black"): "♝",
    ("queen",  "white"): "♕", ("queen",  "black"): "♛",
    ("king",   "white"): "♔", ("king",   "black"): "♚",
}

# ---------------------------------------------------------------------------
# Fullscreen button  (fixed pixel size, drawn on top of everything)
# ---------------------------------------------------------------------------

def fullscreen_btn_rect(win_w: int, win_h: int) -> pygame.Rect:
    return pygame.Rect(win_w - 36, 8, 28, 28)


def draw_fullscreen_btn(screen: pygame.Surface, is_fs: bool,
                        mx: int, my: int) -> None:
    r = fullscreen_btn_rect(*screen.get_size())
    pygame.draw.rect(screen, (80, 80, 110) if r.collidepoint(mx, my) else (50, 50, 70),
                     r, border_radius=4)
    _draw_fs_icon(screen, r, is_fs)


def _draw_fs_icon(screen: pygame.Surface, r: pygame.Rect, is_fs: bool) -> None:
    pad, arm, lw = 5, 5, 2
    x1, y1 = r.left  + pad, r.top    + pad
    x2, y2 = r.right - pad - 1, r.bottom - pad - 1
    c = C_TEXT
    if not is_fs:
        pygame.draw.lines(screen, c, False, [(x1 + arm, y1), (x1, y1), (x1, y1 + arm)], lw)
        pygame.draw.lines(screen, c, False, [(x2 - arm, y1), (x2, y1), (x2, y1 + arm)], lw)
        pygame.draw.lines(screen, c, False, [(x1 + arm, y2), (x1, y2), (x1, y2 - arm)], lw)
        pygame.draw.lines(screen, c, False, [(x2 - arm, y2), (x2, y2), (x2, y2 - arm)], lw)
    else:
        cx, cy = r.centerx, r.centery
        d = (x2 - x1) // 4
        ix1, iy1 = cx - d, cy - d
        ix2, iy2 = cx + d, cy + d
        pygame.draw.lines(screen, c, False, [(ix1 - arm, iy1), (ix1, iy1), (ix1, iy1 - arm)], lw)
        pygame.draw.lines(screen, c, False, [(ix2 + arm, iy1), (ix2, iy1), (ix2, iy1 - arm)], lw)
        pygame.draw.lines(screen, c, False, [(ix1 - arm, iy2), (ix1, iy2), (ix1, iy2 + arm)], lw)
        pygame.draw.lines(screen, c, False, [(ix2 + arm, iy2), (ix2, iy2), (ix2, iy2 + arm)], lw)

# ---------------------------------------------------------------------------
# Renderer class
# ---------------------------------------------------------------------------

class Renderer:
    def __init__(self, player_color: str | None, display: dict | None = None):
        self.player_color = player_color
        self._display  = display or {}
        self._flipped  = (player_color == "black")
        self._scale   = 1.0
        self._win_w   = WIN_W
        self._win_h   = WIN_H
        self._sq      = SQ
        self._board_x = _BASE_BOARD_X
        self._board_y = _BASE_BOARD_Y
        self._piece_r = PIECE_R
        self._mana_h  = _BASE_MANA_H
        self._gap     = 4
        self._make_fonts()

    def _make_fonts(self) -> None:
        self._font_piece = _load_font(max(10, int(self._piece_r * 1.5)))
        self._font_ui    = _load_font(max(8,  int(16 * self._scale)))
        self._font_big   = _load_font(max(12, int(36 * self._scale)))
        self._font_huge  = _load_font(max(24, int(120 * self._scale)))

    def _update_layout(self, win_w: int, win_h: int) -> None:
        scale = min(win_w / WIN_W, win_h / WIN_H)
        if abs(scale - self._scale) < 0.005 and (win_w, win_h) == (self._win_w, self._win_h):
            return
        self._scale   = scale
        self._win_w   = win_w
        self._win_h   = win_h
        self._sq      = max(1,  int(SQ           * scale))
        self._piece_r = max(4,  int(PIECE_R      * scale))
        self._mana_h  = max(4,  int(_BASE_MANA_H * scale))
        self._gap     = max(2,  int(4            * scale))
        lw    = int(WIN_W * scale)
        lh    = int(WIN_H * scale)
        off_x = (win_w - lw) // 2
        off_y = (win_h - lh) // 2
        self._board_x = off_x + int(_BASE_BOARD_X * scale)
        self._board_y = off_y + int(_BASE_BOARD_Y * scale)
        self._make_fonts()

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def toggle_flip(self) -> None:
        self._flipped = not self._flipped

    def board_to_px(self, bx: float, by: float) -> tuple[int, int]:
        if self._flipped:
            return (int(self._board_x + (8.0 - bx) * self._sq),
                    int(self._board_y + (8.0 - by) * self._sq))
        return (int(self._board_x + bx * self._sq),
                int(self._board_y + by * self._sq))

    def px_to_board(self, px: int, py: int) -> tuple[float, float]:
        if self._flipped:
            return (8.0 - (px - self._board_x) / self._sq,
                    8.0 - (py - self._board_y) / self._sq)
        return ((px - self._board_x) / self._sq,
                (py - self._board_y) / self._sq)

    def _board_dir_to_angle(self, lx: float, ly: float) -> float:
        """Board-space direction → screen angle, corrected for board flip."""
        if self._flipped:
            return math.atan2(-ly, -lx)
        return math.atan2(ly, lx)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def render(self, screen: pygame.Surface, state: dict,
               selected_id: str | None, snap_max: float = 0.0,
               debug: bool = False,
               drag_id: str | None = None,
               drag_px_pos: tuple | None = None) -> None:
        self._update_layout(*screen.get_size())
        screen.fill(C_BG)
        self._draw_board(screen)
        self._draw_dest_markers(screen, state)
        self._draw_pieces(screen, state, selected_id, drag_id, drag_px_pos)
        self._draw_move_hints(screen, state, selected_id, snap_max, debug)
        self._draw_mana_bars(screen, state)
        countdown = state.get("countdown")
        if countdown is not None:
            self._draw_countdown(screen, countdown)
        elif state.get("game_over"):
            self._draw_game_over(screen, state.get("winner"))

    def render_waiting(self, screen: pygame.Surface) -> None:
        self._update_layout(*screen.get_size())
        screen.fill(C_BG)
        self._draw_board(screen)
        t = self._font_ui.render("Waiting for opponent…", True, C_TEXT)
        screen.blit(t, (self._win_w // 2 - t.get_width() // 2,
                        self._win_h // 2 - t.get_height() // 2))

    # ------------------------------------------------------------------
    # Movement hints
    # ------------------------------------------------------------------

    def _draw_move_hints(self, screen: pygame.Surface, state: dict,
                          selected_id: str | None,
                          snap_max: float = 0.0,
                          debug: bool = False) -> None:
        if not selected_id:
            return
        piece = next((p for p in state["pieces"] if p["id"] == selected_id), None)
        if not piece or piece["state"] != "idle":
            return

        bx, by   = piece["x"], piece["y"]
        cx, cy   = self.board_to_px(bx, by)
        ptype    = piece["type"]
        owner    = piece["owner"]
        fr       = math.radians(_pval(state.get("freedom_deg", 5.0), owner, 5.0))
        mana     = state.get("mana", {}).get(owner, 0.0)
        pp       = state.get("player_params", {}).get(owner, {})
        base_cost = pp.get("base_move_cost", _BASE_MOVE_COST)
        dist_cost = pp.get("distance_cost", _DISTANCE_COST)
        max_dist = max(0.0, (mana - base_cost) / dist_cost) if dist_cost > 1e-9 else 8.0
        mana_r   = max_dist * self._sq

        surf = pygame.Surface((self._win_w, self._win_h), pygame.SRCALPHA)
        surf.set_clip(pygame.Rect(self._board_x, self._board_y,
                                   8 * self._sq, 8 * self._sq))

        if snap_max > 0 and debug:
            self._draw_snap_zones(surf, piece, bx, by, cx, cy, ptype, owner, fr, snap_max)

        if ptype == "knight":
            r_px = max(4, int(math.sqrt(5.0) * math.tan(fr) * self._sq))
            for a, b in [(2,1),(2,-1),(-2,1),(-2,-1),(1,2),(1,-2),(-1,2),(-1,-2)]:
                tx, ty = bx + a, by + b
                if 0 <= tx <= 8 and 0 <= ty <= 8:
                    px2, py2 = self.board_to_px(tx, ty)
                    color = C_HINT_OK if math.hypot(a, b) <= max_dist else C_HINT_NO_MANA
                    pygame.draw.circle(surf, color, (px2, py2), r_px)

        elif ptype == "pawn":
            fwd     = -1.0 if owner == "white" else 1.0
            max_fwd = 1.0  if piece.get("has_moved") else 2.0
            _wedge_mana(surf, cx, cy, self._board_dir_to_angle(0.0, fwd), fr, max_fwd * self._sq, mana_r)

            # Diagonal capture circles (like knight), centered one diagonal square away
            diag_r_board = _SQRT2 * math.tan(fr)   # circle radius in board units
            diag_r_px    = max(4, int(diag_r_board * self._sq))
            diag_color   = C_HINT_OK if _SQRT2 <= max_dist else C_HINT_NO_MANA

            for xdir in (1.0, -1.0):
                ccx_b = bx + xdir
                ccy_b = by + fwd
                if not (0.0 < ccx_b < 8.0 and 0.0 < ccy_b < 8.0):
                    continue
                ccx_px, ccy_px = self.board_to_px(ccx_b, ccy_b)

                # Full circle in red; valid arcs drawn on top per nearby piece
                pygame.draw.circle(surf, C_HINT_ILLEGAL, (ccx_px, ccy_px), diag_r_px)

                for other in state["pieces"]:
                    if other.get("id") == piece.get("id"):
                        continue
                    if other.get("owner") == owner:
                        continue
                    odx = other["x"] - ccx_b
                    ody = other["y"] - ccy_b
                    other_d = math.hypot(odx, ody)
                    if other_d > diag_r_board + _DIAMETER_PIECE + 1e-6:
                        continue
                    if other_d < 1e-9:
                        alpha = math.pi
                    else:
                        cos_a = ((diag_r_board**2 + other_d**2 - _DIAMETER_PIECE**2)
                                 / (2 * diag_r_board * other_d))
                        if cos_a >= 1.0:
                            continue
                        alpha = math.acos(max(-1.0, cos_a))
                    screen_angle = self._board_dir_to_angle(odx, ody)
                    _wedge(surf, ccx_px, ccy_px, screen_angle, alpha, diag_r_px, diag_color)

        elif ptype == "king":
            unmoved = not piece.get("has_moved", False)
            for lx, ly in _ALL8:
                cap = (2.0 if (ly == 0.0 and unmoved)
                       else (1.0 if (lx == 0.0 or ly == 0.0) else _SQRT2))
                _wedge_mana(surf, cx, cy, self._board_dir_to_angle(lx, ly), fr, cap * self._sq, mana_r)

        else:
            dirs = {"rook": _ORTHO, "bishop": _DIAG, "queen": _ALL8}.get(ptype, [])
            for lx, ly in dirs:
                full_r = _max_to_edge(bx, by, lx, ly) * self._sq
                _wedge_mana(surf, cx, cy, self._board_dir_to_angle(lx, ly), fr, full_r, mana_r)

        screen.blit(surf, (0, 0))

    def _draw_snap_zones(self, surf: pygame.Surface,
                          piece: dict, bx: float, by: float,
                          cx: float, cy: float,
                          ptype: str, owner: str,
                          fr: float, snap_max: float) -> None:
        snap_px = snap_max * self._sq

        if ptype == "knight":
            landing_r_px = math.sqrt(5.0) * math.tan(fr) * self._sq
            r = max(1, round(landing_r_px + snap_px))
            for a, b in [(2,1),(2,-1),(-2,1),(-2,-1),(1,2),(1,-2),(-1,2),(-1,-2)]:
                tx, ty = bx + a, by + b
                if 0 <= tx <= 8 and 0 <= ty <= 8:
                    px2, py2 = self.board_to_px(tx, ty)
                    pygame.draw.circle(surf, C_SNAP_ZONE, (px2, py2), r)

        elif ptype == "pawn":
            fwd = -1.0 if owner == "white" else 1.0
            max_fwd = 1.0 if piece.get("has_moved") else 2.0
            # Forward: sector snap zone
            pts = _snap_zone_pts(cx, cy, self._board_dir_to_angle(0.0, fwd),
                                 fr, min(max_fwd, _max_to_edge(bx, by, 0.0, fwd)) * self._sq,
                                 snap_px)
            if len(pts) >= 3:
                pygame.draw.polygon(surf, C_SNAP_ZONE, pts)
            # Diagonal: circle snap zones (like knight)
            landing_r_px = _SQRT2 * math.tan(fr) * self._sq
            r = max(1, round(landing_r_px + snap_px))
            for xdir in (1.0, -1.0):
                tx, ty = bx + xdir, by + fwd
                if 0.0 < tx < 8.0 and 0.0 < ty < 8.0:
                    px2, py2 = self.board_to_px(tx, ty)
                    pygame.draw.circle(surf, C_SNAP_ZONE, (px2, py2), r)

        elif ptype == "king":
            unmoved = not piece.get("has_moved", False)
            for lx, ly in _ALL8:
                cap = (2.0 if (ly == 0.0 and unmoved)
                       else (1.0 if (lx == 0.0 or ly == 0.0) else _SQRT2))
                max_t = min(cap, _max_to_edge(bx, by, lx, ly))
                pts = _snap_zone_pts(cx, cy, self._board_dir_to_angle(lx, ly),
                                     fr, max_t * self._sq, snap_px)
                if len(pts) >= 3:
                    pygame.draw.polygon(surf, C_SNAP_ZONE, pts)

        else:
            dirs = {"rook": _ORTHO, "bishop": _DIAG, "queen": _ALL8}.get(ptype, [])
            for lx, ly in dirs:
                max_t = _max_to_edge(bx, by, lx, ly)
                pts = _snap_zone_pts(cx, cy, self._board_dir_to_angle(lx, ly),
                                     fr, max_t * self._sq, snap_px)
                if len(pts) >= 3:
                    pygame.draw.polygon(surf, C_SNAP_ZONE, pts)

    # ------------------------------------------------------------------
    # Board
    # ------------------------------------------------------------------

    def _draw_board(self, screen: pygame.Surface) -> None:
        sq = self._sq
        bx = self._board_x
        by = self._board_y
        for row in range(8):
            for col in range(8):
                color = C_LIGHT if (row + col) % 2 == 0 else C_DARK
                pygame.draw.rect(screen, color, (bx + col * sq, by + row * sq, sq, sq))
        for i in range(8):
            col_ch  = "abcdefgh"[7 - i] if self._flipped else "abcdefgh"[i]
            row_str = str(i + 1)        if self._flipped else str(8 - i)
            lbl = self._font_ui.render(col_ch, True, C_TEXT)
            screen.blit(lbl, (bx + i * sq + sq // 2 - lbl.get_width() // 2,
                               by + 8 * sq + self._gap))
            lbl = self._font_ui.render(row_str, True, C_TEXT)
            screen.blit(lbl, (bx - lbl.get_width() - self._gap,
                               by + i * sq + sq // 2 - lbl.get_height() // 2))
        pygame.draw.rect(screen, C_BOARD_BORDER, (bx, by, 8 * sq, 8 * sq),
                         max(1, int(2 * self._scale)))

    # ------------------------------------------------------------------
    # Destination markers
    # ------------------------------------------------------------------

    def _draw_dest_markers(self, screen: pygame.Surface, state: dict) -> None:
        mr = max(3, int(7 * self._scale))
        for p in state["pieces"]:
            if p["state"] not in ("preparation", "moving"):
                continue
            if p["type"] == "ghost":
                continue
            cx, cy = self.board_to_px(p["dest_x"], p["dest_y"])
            pygame.draw.circle(screen, C_DEST_MARKER, (cx, cy), mr)
            border = C_BLACK_BORDER if p["owner"] == "white" else C_WHITE_BORDER
            pygame.draw.circle(screen, border, (cx, cy), mr, max(1, mr // 4))

    # ------------------------------------------------------------------
    # Pieces
    # ------------------------------------------------------------------

    def _draw_pieces(self, screen: pygame.Surface, state: dict,
                     selected_id: str | None,
                     drag_id: str | None = None,
                     drag_px_pos: tuple | None = None) -> None:
        sel_ring   = self._piece_r + max(2, int(5 * self._scale))

        dragged_piece = None
        for p in state["pieces"]:
            if drag_id is not None and p["id"] == drag_id:
                dragged_piece = p
                continue  # draw on top after all other pieces

            cx, cy = self.board_to_px(p["x"], p["y"])

            if p["type"] == "ghost":
                self._draw_ghost(screen, cx, cy)
                continue

            fill, border = (C_WHITE_FILL, C_BLACK_BORDER) if p["owner"] == "white" \
                           else (C_BLACK_FILL, C_WHITE_BORDER)

            if p["id"] == selected_id:
                pygame.draw.circle(screen, C_SELECT, (cx, cy), sel_ring)

            pygame.draw.circle(screen, fill,   (cx, cy), self._piece_r)
            pygame.draw.circle(screen, border, (cx, cy), self._piece_r, 2)

            icon_color = C_WHITE_ICON if p["owner"] == "white" else C_BLACK_ICON
            t = self._font_piece.render(ICONS.get((p["type"], p["owner"]), "?"), True, icon_color)
            screen.blit(t, (cx - t.get_width() // 2, cy - t.get_height() // 2))

            if p["state"] == "cooldown":
                s = pygame.Surface((self._piece_r * 2, self._piece_r * 2), pygame.SRCALPHA)
                pygame.draw.circle(s, (0, 0, 0, 100),
                                   (self._piece_r, self._piece_r), self._piece_r)
                screen.blit(s, (cx - self._piece_r, cy - self._piece_r))

            is_own  = (self.player_color is None or p["owner"] == self.player_color)
            show    = (is_own     and self._display.get("show_own_timers")) or \
                      (not is_own and self._display.get("show_opp_timers"))
            if show:
                timer      = p.get("state_timer", 0.0)
                prep_total = _pval(state.get("prep_period", 0.5), p["owner"], 0.5)
                cool_total = _pval(state.get("cooldown",    0.8), p["owner"], 0.8)
                if p["state"] == "preparation" and prep_total > 0:
                    self._draw_timer_arc(screen, cx, cy,
                                         1.0 - timer / prep_total, C_TIMER_PREP)
                elif p["state"] == "cooldown" and cool_total > 0:
                    self._draw_timer_arc(screen, cx, cy,
                                         1.0 - timer / cool_total, C_TIMER_COOL)

        if dragged_piece is not None and drag_px_pos is not None:
            p = dragged_piece
            cx, cy = drag_px_pos
            fill, border = (C_WHITE_FILL, C_BLACK_BORDER) if p["owner"] == "white" \
                           else (C_BLACK_FILL, C_WHITE_BORDER)
            pygame.draw.circle(screen, fill,   (cx, cy), self._piece_r)
            pygame.draw.circle(screen, border, (cx, cy), self._piece_r, 2)
            icon_color = C_WHITE_ICON if p["owner"] == "white" else C_BLACK_ICON
            t = self._font_piece.render(ICONS.get((p["type"], p["owner"]), "?"), True, icon_color)
            screen.blit(t, (cx - t.get_width() // 2, cy - t.get_height() // 2))

    def _draw_timer_arc(self, screen: pygame.Surface, cx: int, cy: int,
                        fraction: float, color: tuple) -> None:
        """Clockwise ring-arc from 12 o'clock filling to `fraction` of full circle."""
        if fraction <= 0:
            return
        fraction = min(1.0, fraction)
        r_out = self._piece_r + max(3, int(5 * self._scale))
        r_in  = self._piece_r + max(1, int(2 * self._scale))
        n     = max(4, int(48 * fraction))
        start = -math.pi / 2
        sweep = fraction * 2 * math.pi
        outer, inner = [], []
        for i in range(n + 1):
            a = start + sweep * i / n
            ca, sa = math.cos(a), math.sin(a)
            outer.append((cx + ca * r_out, cy + sa * r_out))
            inner.append((cx + ca * r_in,  cy + sa * r_in))
        pygame.draw.polygon(screen, color, outer + inner[::-1])

    def _draw_ghost(self, screen: pygame.Surface, cx: int, cy: int) -> None:
        r = self._piece_r
        s = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
        pygame.draw.circle(s, (*C_GHOST_FILL, 110), (r, r), r)
        pygame.draw.circle(s, (*C_GHOST_FILL, 200), (r, r), r, 2)
        screen.blit(s, (cx - r, cy - r))

    # ------------------------------------------------------------------
    # Mana bars
    # ------------------------------------------------------------------

    def _draw_mana_bars(self, screen: pygame.Surface, state: dict) -> None:
        mana  = state.get("mana", {})
        bar_w = 8 * self._sq
        mh    = self._mana_h
        gap   = max(2, int(6 * self._scale))

        self._draw_one_mana(screen, mana.get("black", 0.0), "black",
                             self._board_x, self._board_y - mh - gap,
                             bar_w, _pval(state.get("max_mana", 5.0), "black", 5.0), mh)
        self._draw_one_mana(screen, mana.get("white", 0.0), "white",
                             self._board_x, self._board_y + 8 * self._sq + gap,
                             bar_w, _pval(state.get("max_mana", 5.0), "white", 5.0), mh)

    def _draw_one_mana(self, screen: pygame.Surface, value: float,
                        owner: str, x: int, y: int, w: int,
                        max_mana: float, mh: int) -> None:
        fill_w    = int(w * max(0.0, value) / max_mana)
        bar_color = C_MANA_BLACK if owner == "black" else C_MANA_WHITE
        pygame.draw.rect(screen, C_MANA_BG,   (x, y, w, mh))
        pygame.draw.rect(screen, bar_color,    (x, y, fill_w, mh))
        pygame.draw.rect(screen, C_TEXT,       (x, y, w, mh), 1)
        lbl = self._font_ui.render(
            f"{owner.capitalize()}  {value:.1f} / {max_mana:.0f}", True, C_TEXT)
        screen.blit(lbl, (x + self._gap, y + mh // 2 - lbl.get_height() // 2))

    # ------------------------------------------------------------------
    # Countdown overlay
    # ------------------------------------------------------------------

    def _draw_countdown(self, screen: pygame.Surface, n: int) -> None:
        overlay = pygame.Surface((self._win_w, self._win_h), pygame.SRCALPHA)
        overlay.fill(C_OVERLAY)
        screen.blit(overlay, (0, 0))
        msg = "Go!" if n == 0 else str(n)
        t = self._font_huge.render(msg, True, C_WIN_TEXT)
        screen.blit(t, (self._win_w // 2 - t.get_width() // 2,
                        self._win_h // 2 - t.get_height() // 2))

    # ------------------------------------------------------------------
    # Game over overlay
    # ------------------------------------------------------------------

    def _draw_game_over(self, screen: pygame.Surface, winner: str | None) -> None:
        overlay = pygame.Surface((self._win_w, self._win_h), pygame.SRCALPHA)
        overlay.fill(C_OVERLAY)
        screen.blit(overlay, (0, 0))

        if winner == "draw":
            msg = "Draw!"
        elif winner:
            msg = f"{winner.capitalize()} wins!"
        else:
            msg = "Game Over"

        t = self._font_big.render(msg, True, C_WIN_TEXT)
        screen.blit(t, (self._win_w // 2 - t.get_width() // 2,
                        self._win_h // 2 - t.get_height() // 2))

        sub = self._font_ui.render("Press Escape to return to menu", True, C_TEXT)
        screen.blit(sub, (self._win_w // 2 - sub.get_width() // 2,
                           self._win_h // 2 + t.get_height()))
