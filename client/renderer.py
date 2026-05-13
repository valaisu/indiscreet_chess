"""
All pygame drawing code. No game logic here.

Board coordinate system (matches server):
  (0, 0) = top-left (black back rank)
  (8, 8) = bottom-right (white back rank)
  Each square has side = SQUARE_SIDE board units = SQ pixels.
"""

import math

import pygame

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

SQ = 80          # pixels per board unit (SQUARE_SIDE = 1.0)
BOARD_X = 80     # board left edge in pixels
BOARD_Y = 100    # board top edge (leaves room for black mana bar above)
PIECE_R = int(0.3 * SQ)   # piece radius in pixels (= DIAMETER_PIECE/2 * SQ)

WIN_W = 2 * BOARD_X + 8 * SQ   # 800
WIN_H = BOARD_Y + 8 * SQ + 100  # 840

MANA_H = 22        # mana bar height in pixels
MANA_MAX = 10.0    # must match server params.MAXIMUM_MANA

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

C_BG           = (30,  30,  30)
C_LIGHT        = (240, 217, 181)
C_DARK         = (181, 136,  99)
C_BOARD_BORDER = (100,  80,  60)

C_WHITE_FILL   = (255, 255, 240)
C_BLACK_FILL   = ( 45,  45,  45)
C_WHITE_BORDER = (160, 160, 160)
C_BLACK_BORDER = (210, 210, 210)

C_SELECT       = ( 80, 210,  80)   # selection ring
C_DEST_MARKER  = (220, 200,  50)   # destination dot
C_GHOST_FILL   = (160, 160, 200)   # ghost piece (also drawn semi-transparent)

C_MANA_BG      = ( 25,  25,  55)
C_MANA_WHITE   = ( 70, 130, 200)
C_MANA_BLACK   = (180,  60,  60)

C_TEXT         = (220, 220, 220)
C_OVERLAY      = (  0,   0,   0, 160)
C_WIN_TEXT     = (255, 220, 100)
C_HINT         = (100, 210, 100,  45)   # move-hint wedge fill

_SQRT2       = math.sqrt(2.0)
_FREEDOM_RAD = math.radians(5.0)
_ORTHO = [(1, 0), (-1, 0), (0, 1), (0, -1)]
_DIAG  = [( 1/_SQRT2,  1/_SQRT2), ( 1/_SQRT2, -1/_SQRT2),
          (-1/_SQRT2,  1/_SQRT2), (-1/_SQRT2, -1/_SQRT2)]
_ALL8  = _ORTHO + _DIAG


def _max_to_edge(bx: float, by: float, lx: float, ly: float) -> float:
    """Board units from (bx, by) to the board edge in direction (lx, ly)."""
    t = 8.0
    if lx > 1e-9:    t = min(t, (8.0 - bx) / lx)
    elif lx < -1e-9: t = min(t, bx / -lx)
    if ly > 1e-9:    t = min(t, (8.0 - by) / ly)
    elif ly < -1e-9: t = min(t, by / -ly)
    return max(0.0, t)


def _wedge(surf: pygame.Surface, cx: float, cy: float,
           angle: float, half: float, r: float, n: int = 12) -> None:
    """Draw a filled wedge on surf centred at (cx,cy)."""
    if r < 1:
        return
    pts = [(cx, cy)]
    for i in range(n + 1):
        a = angle - half + 2 * half * i / n
        pts.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
    pygame.draw.polygon(surf, C_HINT, pts)

# ---------------------------------------------------------------------------
# Piece labels
# ---------------------------------------------------------------------------

LABELS = {
    "pawn": "P", "rook": "R", "knight": "N",
    "bishop": "B", "queen": "Q", "king": "K",
}

# ---------------------------------------------------------------------------
# Coordinate helpers (used by main.py too)
# ---------------------------------------------------------------------------

def board_to_px(bx: float, by: float) -> tuple[int, int]:
    return (int(BOARD_X + bx * SQ), int(BOARD_Y + by * SQ))


def px_to_board(px: int, py: int) -> tuple[float, float]:
    return ((px - BOARD_X) / SQ, (py - BOARD_Y) / SQ)


# ---------------------------------------------------------------------------
# Renderer class
# ---------------------------------------------------------------------------

class Renderer:
    def __init__(self, player_color: str | None):
        """
        player_color: "white" | "black" — whose perspective (affects mana bar
        label colours and selectable pieces).  None means solo (control both).
        """
        self.player_color = player_color
        self._font_piece = pygame.font.SysFont("dejavusans,arial,sans-serif", PIECE_R)
        self._font_ui    = pygame.font.SysFont("dejavusans,arial,sans-serif", 16)
        self._font_big   = pygame.font.SysFont("dejavusans,arial,sans-serif", 36)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def render(self, screen: pygame.Surface, state: dict,
               selected_id: str | None) -> None:
        screen.fill(C_BG)
        self._draw_board(screen)
        self._draw_move_hints(screen, state, selected_id)
        self._draw_dest_markers(screen, state)
        self._draw_pieces(screen, state, selected_id)
        self._draw_mana_bars(screen, state)
        if state.get("game_over"):
            self._draw_game_over(screen, state.get("winner"))

    def render_waiting(self, screen: pygame.Surface) -> None:
        screen.fill(C_BG)
        self._draw_board(screen)
        t = self._font_ui.render("Waiting for opponent…", True, C_TEXT)
        screen.blit(t, (WIN_W // 2 - t.get_width() // 2,
                        WIN_H // 2 - t.get_height() // 2))

    # ------------------------------------------------------------------
    # Movement hints (drawn on board after squares, before pieces)
    # ------------------------------------------------------------------

    def _draw_move_hints(self, screen: pygame.Surface, state: dict,
                          selected_id: str | None) -> None:
        if not selected_id:
            return
        piece = next((p for p in state["pieces"] if p["id"] == selected_id), None)
        if not piece or piece["state"] != "idle":
            return

        bx, by = piece["x"], piece["y"]
        cx, cy = board_to_px(bx, by)
        ptype  = piece["type"]
        fr     = math.radians(state.get("freedom_deg", 5.0))

        surf = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        surf.set_clip(pygame.Rect(BOARD_X, BOARD_Y, 8 * SQ, 8 * SQ))

        if ptype == "knight":
            s = 1.0
            r_px = max(4, int(math.sqrt(5.0) * s * math.tan(fr) * SQ))
            for a, b in [(2,1),(2,-1),(-2,1),(-2,-1),(1,2),(1,-2),(-1,2),(-1,-2)]:
                tx, ty = bx + a * s, by + b * s
                if 0 <= tx <= 8 and 0 <= ty <= 8:
                    px2, py2 = board_to_px(tx, ty)
                    pygame.draw.circle(surf, C_HINT, (px2, py2), r_px)

        elif ptype == "pawn":
            fwd = -1.0 if piece["owner"] == "white" else 1.0
            max_fwd = 1.0 if piece.get("has_moved") else 2.0
            _wedge(surf, cx, cy, math.atan2(fwd, 0.0), fr, max_fwd * SQ)
            for xdir in (1.0, -1.0):
                a = math.atan2(fwd / _SQRT2, xdir / _SQRT2)
                _wedge(surf, cx, cy, a, fr, _SQRT2 * SQ)

        elif ptype == "king":
            unmoved = not piece.get("has_moved", False)
            for lx, ly in _ALL8:
                is_horiz = (ly == 0.0)
                cap = (2.0 if (is_horiz and unmoved)
                       else (1.0 if (lx == 0.0 or ly == 0.0) else _SQRT2))
                _wedge(surf, cx, cy, math.atan2(ly, lx), fr, cap * SQ)

        else:
            dirs = {"rook": _ORTHO, "bishop": _DIAG, "queen": _ALL8}.get(ptype, [])
            for lx, ly in dirs:
                r = _max_to_edge(bx, by, lx, ly) * SQ
                _wedge(surf, cx, cy, math.atan2(ly, lx), fr, r)

        screen.blit(surf, (0, 0))

    # ------------------------------------------------------------------
    # Board
    # ------------------------------------------------------------------

    def _draw_board(self, screen: pygame.Surface) -> None:
        for row in range(8):
            for col in range(8):
                color = C_LIGHT if (row + col) % 2 == 0 else C_DARK
                pygame.draw.rect(screen, color,
                                 (BOARD_X + col * SQ, BOARD_Y + row * SQ, SQ, SQ))
        # Rank/file labels
        for i in range(8):
            # Files: a–h along bottom
            lbl = self._font_ui.render("abcdefgh"[i], True, C_TEXT)
            screen.blit(lbl, (BOARD_X + i * SQ + SQ // 2 - lbl.get_width() // 2,
                               BOARD_Y + 8 * SQ + 4))
            # Ranks: 8–1 along left
            lbl = self._font_ui.render(str(8 - i), True, C_TEXT)
            screen.blit(lbl, (BOARD_X - lbl.get_width() - 4,
                               BOARD_Y + i * SQ + SQ // 2 - lbl.get_height() // 2))
        pygame.draw.rect(screen, C_BOARD_BORDER,
                         (BOARD_X, BOARD_Y, 8 * SQ, 8 * SQ), 2)

    # ------------------------------------------------------------------
    # Destination markers
    # ------------------------------------------------------------------

    def _draw_dest_markers(self, screen: pygame.Surface, state: dict) -> None:
        for p in state["pieces"]:
            if p["state"] not in ("preparation", "moving"):
                continue
            if p["type"] == "ghost":
                continue
            cx, cy = board_to_px(p["dest_x"], p["dest_y"])
            pygame.draw.circle(screen, C_DEST_MARKER, (cx, cy), 7)
            border = C_BLACK_BORDER if p["owner"] == "white" else C_WHITE_BORDER
            pygame.draw.circle(screen, border, (cx, cy), 7, 2)

    # ------------------------------------------------------------------
    # Pieces
    # ------------------------------------------------------------------

    def _draw_pieces(self, screen: pygame.Surface, state: dict,
                     selected_id: str | None) -> None:
        for p in state["pieces"]:
            cx, cy = board_to_px(p["x"], p["y"])

            if p["type"] == "ghost":
                self._draw_ghost(screen, cx, cy)
                continue

            fill, border = self._piece_colors(p)
            is_selected = (p["id"] == selected_id)

            # Selection ring (drawn first, behind piece)
            if is_selected:
                pygame.draw.circle(screen, C_SELECT, (cx, cy), PIECE_R + 5)

            # Body
            pygame.draw.circle(screen, fill, (cx, cy), PIECE_R)
            pygame.draw.circle(screen, border, (cx, cy), PIECE_R, 2)

            # Label
            label = LABELS.get(p["type"], "?")
            t = self._font_piece.render(label, True, border)
            screen.blit(t, (cx - t.get_width() // 2, cy - t.get_height() // 2))

            # Cooldown: dim overlay
            if p["state"] == "cooldown":
                s = pygame.Surface((PIECE_R * 2, PIECE_R * 2), pygame.SRCALPHA)
                pygame.draw.circle(s, (0, 0, 0, 100), (PIECE_R, PIECE_R), PIECE_R)
                screen.blit(s, (cx - PIECE_R, cy - PIECE_R))

    def _piece_colors(self, p: dict) -> tuple[tuple, tuple]:
        if p["owner"] == "white":
            return C_WHITE_FILL, C_BLACK_BORDER
        else:
            return C_BLACK_FILL, C_WHITE_BORDER

    def _draw_ghost(self, screen: pygame.Surface, cx: int, cy: int) -> None:
        s = pygame.Surface((PIECE_R * 2, PIECE_R * 2), pygame.SRCALPHA)
        pygame.draw.circle(s, (*C_GHOST_FILL, 110), (PIECE_R, PIECE_R), PIECE_R)
        pygame.draw.circle(s, (*C_GHOST_FILL, 200), (PIECE_R, PIECE_R), PIECE_R, 2)
        screen.blit(s, (cx - PIECE_R, cy - PIECE_R))

    # ------------------------------------------------------------------
    # Mana bars
    # ------------------------------------------------------------------

    def _draw_mana_bars(self, screen: pygame.Surface, state: dict) -> None:
        mana    = state.get("mana", {})
        max_mana = state.get("max_mana", MANA_MAX)
        bar_w   = 8 * SQ

        self._draw_one_mana(screen, mana.get("black", 0.0), "black",
                             BOARD_X, BOARD_Y - MANA_H - 6, bar_w, max_mana)
        self._draw_one_mana(screen, mana.get("white", 0.0), "white",
                             BOARD_X, BOARD_Y + 8 * SQ + 6, bar_w, max_mana)

    def _draw_one_mana(self, screen: pygame.Surface, value: float,
                        owner: str, x: int, y: int, w: int,
                        max_mana: float) -> None:
        fill_w = int(w * max(0.0, value) / max_mana)
        bar_color = C_MANA_BLACK if owner == "black" else C_MANA_WHITE
        pygame.draw.rect(screen, C_MANA_BG, (x, y, w, MANA_H))
        pygame.draw.rect(screen, bar_color, (x, y, fill_w, MANA_H))
        pygame.draw.rect(screen, C_TEXT, (x, y, w, MANA_H), 1)
        lbl = self._font_ui.render(f"{owner.capitalize()}  {value:.1f} / {max_mana:.0f}",
                                    True, C_TEXT)
        screen.blit(lbl, (x + 4, y + MANA_H // 2 - lbl.get_height() // 2))

    # ------------------------------------------------------------------
    # Game over overlay
    # ------------------------------------------------------------------

    def _draw_game_over(self, screen: pygame.Surface,
                         winner: str | None) -> None:
        overlay = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        overlay.fill(C_OVERLAY)
        screen.blit(overlay, (0, 0))

        if winner == "draw":
            msg = "Draw!"
        elif winner:
            msg = f"{winner.capitalize()} wins!"
        else:
            msg = "Game Over"

        t = self._font_big.render(msg, True, C_WIN_TEXT)
        screen.blit(t, (WIN_W // 2 - t.get_width() // 2,
                        WIN_H // 2 - t.get_height() // 2))

        sub = self._font_ui.render("Press Escape to return to menu", True, C_TEXT)
        screen.blit(sub, (WIN_W // 2 - sub.get_width() // 2,
                           WIN_H // 2 + t.get_height()))
