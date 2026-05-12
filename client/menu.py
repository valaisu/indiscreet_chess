"""Start menu: mode selection and parameter configuration."""
import sys
import pygame

from client.renderer import WIN_W, WIN_H

# ── Colors ────────────────────────────────────────────────────────────────────
_C_BG     = ( 30,  30,  30)
_C_TEXT   = (220, 220, 220)
_C_ACTIVE = ( 70, 130, 200)
_C_BTN    = ( 55,  55,  75)
_C_BTN_H  = ( 80,  80, 110)
_C_SEP    = ( 70,  70,  70)
_C_INPUT  = ( 45,  45,  60)
_C_START  = ( 60, 120,  60)
_C_START_H= ( 90, 160,  90)

# ── Parameter table: (display label, key, min, max, step) ────────────────────
_PARAMS = [
    ("Mana refill / s",  "mana_refill_rate",     0.1,  5.0, 0.10),
    ("Maximum mana",     "maximum_mana",          1.0, 20.0, 1.00),
    ("Base move cost",   "base_move_cost",        0.0,  5.0, 0.10),
    ("Distance cost",    "distance_cost",         0.0,  2.0, 0.05),
    ("Prep period (s)",  "preparation_period",    0.0,  3.0, 0.10),
    ("Move speed",       "movement_speed",        1.0, 10.0, 0.50),
    ("Cooldown (s)",     "cooldown",              0.0,  3.0, 0.10),
    ("Freedom (°)",      "movement_freedom_deg",  1.0, 15.0, 1.00),
    ("Piece diameter",   "diameter_piece",        0.3,  1.0, 0.05),
]

DEFAULTS: dict = {
    "mana_refill_rate":      0.3,
    "maximum_mana":          5.0,
    "base_move_cost":        1.0,
    "distance_cost":         0.2,
    "preparation_period":    0.5,
    "movement_speed":        4.0,
    "cooldown":              0.8,
    "movement_freedom_deg":  5.0,
    "diameter_piece":        0.6,
}

# ── Layout constants ──────────────────────────────────────────────────────────
_CX      = WIN_W // 2
_TITLE_Y = 42
_MODE_Y  = 100
_SEP_Y   = 150
_BODY_Y  = 168
_ROW_H   = 36
_BTN_W   = 28
_BTN_H   = 28
_LABEL_X = 85
_MINUS_X = 508
_VAL_X   = 578
_PLUS_X  = 638


def _port_y(mode: str) -> int:
    return _BODY_Y + (52 if mode == "join" else len(_PARAMS) * _ROW_H + 8)


def _start_y(mode: str) -> int:
    return _port_y(mode) + 52


def _mode_rect(i: int) -> pygame.Rect:
    w, h, gap = 118, 38, 18
    x = _CX - (3 * w + 2 * gap) // 2 + i * (w + gap)
    return pygame.Rect(x, _MODE_Y, w, h)


def _ip_rect() -> pygame.Rect:
    return pygame.Rect(240, _BODY_Y, 380, 34)


def _port_rect(mode: str) -> pygame.Rect:
    return pygame.Rect(240, _port_y(mode), 120, 34)


def _start_rect(mode: str) -> pygame.Rect:
    return pygame.Rect(_CX - 100, _start_y(mode), 200, 48)


def _fmt(val: float, step: float) -> str:
    if step >= 1.0:  return f"{val:.0f}"
    if step >= 0.1:  return f"{val:.1f}"
    return f"{val:.2f}"


# ── Public entry point ────────────────────────────────────────────────────────

def run_menu(screen: pygame.Surface) -> dict:
    """
    Blocking menu loop.  Returns:
      {"mode": "solo"|"host"|"join", "host_ip": str, "port": int, "params": dict}
    """
    font_big = pygame.font.SysFont("dejavusans,arial,sans-serif", 34)
    font_med = pygame.font.SysFont("dejavusans,arial,sans-serif", 18)
    font_sml = pygame.font.SysFont("dejavusans,arial,sans-serif", 14)

    mode    = "solo"
    vals    = dict(DEFAULTS)
    ip      = "localhost"
    port    = "8765"
    focused = None   # "ip" | "port" | None

    clock = pygame.time.Clock()
    while True:
        clock.tick(60)
        mx, my = pygame.mouse.get_pos()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

            elif event.type == pygame.KEYDOWN:
                if focused == "ip":
                    if event.key == pygame.K_BACKSPACE:
                        ip = ip[:-1]
                    elif event.unicode.isprintable() and len(ip) < 40:
                        ip += event.unicode
                elif focused == "port":
                    if event.key == pygame.K_BACKSPACE:
                        port = port[:-1]
                    elif event.unicode.isdigit() and len(port) < 5:
                        port += event.unicode

            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                focused = None
                action = _click(mx, my, mode)
                if action in ("solo", "host", "join"):
                    mode = action
                elif action == "focus_ip":
                    focused = "ip"
                elif action == "focus_port":
                    focused = "port"
                elif action == "start":
                    return {
                        "mode":    mode,
                        "host_ip": ip.strip() or "localhost",
                        "port":    int(port) if port.isdigit() else 8765,
                        "params":  vals,
                    }
                elif action and action[0] in "+-":
                    _adjust(vals, action[1:], 1 if action[0] == "+" else -1)

        _draw(screen, font_big, font_med, font_sml,
              mode, vals, ip, port, focused, mx, my)
        pygame.display.flip()


# ── Input handling ────────────────────────────────────────────────────────────

def _click(mx: int, my: int, mode: str) -> str | None:
    for i, m in enumerate(("solo", "host", "join")):
        if _mode_rect(i).collidepoint(mx, my):
            return m

    if mode == "join":
        if _ip_rect().collidepoint(mx, my):
            return "focus_ip"
    else:
        for idx, (_, key, *_rest) in enumerate(_PARAMS):
            ry  = _BODY_Y + idx * _ROW_H
            off = (_ROW_H - _BTN_H) // 2
            if pygame.Rect(_MINUS_X, ry + off, _BTN_W, _BTN_H).collidepoint(mx, my):
                return f"-{key}"
            if pygame.Rect(_PLUS_X,  ry + off, _BTN_W, _BTN_H).collidepoint(mx, my):
                return f"+{key}"

    if _port_rect(mode).collidepoint(mx, my):
        return "focus_port"
    if _start_rect(mode).collidepoint(mx, my):
        return "start"
    return None


def _adjust(vals: dict, key: str, direction: int) -> None:
    for _, k, lo, hi, step in _PARAMS:
        if k == key:
            vals[k] = round(max(lo, min(hi, vals[k] + direction * step)), 6)
            return


# ── Drawing ───────────────────────────────────────────────────────────────────

def _draw(screen, font_big, font_med, font_sml,
          mode, vals, ip, port, focused, mx, my) -> None:
    screen.fill(_C_BG)

    # Title
    t = font_big.render("Indiscreet Chess", True, (220, 220, 180))
    screen.blit(t, (_CX - t.get_width() // 2, _TITLE_Y))

    # Mode buttons
    for i, (lbl, m) in enumerate((("Solo", "solo"), ("Host", "host"), ("Join", "join"))):
        r   = _mode_rect(i)
        col = _C_ACTIVE if m == mode else (_C_BTN_H if r.collidepoint(mx, my) else _C_BTN)
        pygame.draw.rect(screen, col, r, border_radius=6)
        t = font_med.render(lbl, True, _C_TEXT)
        screen.blit(t, (r.centerx - t.get_width() // 2, r.centery - t.get_height() // 2))

    # Separator
    pygame.draw.line(screen, _C_SEP, (80, _SEP_Y), (WIN_W - 80, _SEP_Y))

    if mode == "join":
        _draw_field(screen, font_med, "Server IP:", _ip_rect(), ip, focused == "ip", mx, my)
    else:
        for idx, (label, key, _lo, _hi, step) in enumerate(_PARAMS):
            ry = _BODY_Y + idx * _ROW_H
            cy = ry + _ROW_H // 2
            off = (_ROW_H - _BTN_H) // 2

            t = font_sml.render(label, True, _C_TEXT)
            screen.blit(t, (_LABEL_X, cy - t.get_height() // 2))

            for bx_btn, lbl in ((_MINUS_X, "−"), (_PLUS_X, "+")):
                r   = pygame.Rect(bx_btn, ry + off, _BTN_W, _BTN_H)
                col = _C_BTN_H if r.collidepoint(mx, my) else _C_BTN
                pygame.draw.rect(screen, col, r, border_radius=4)
                t = font_med.render(lbl, True, _C_TEXT)
                screen.blit(t, (r.centerx - t.get_width() // 2, r.centery - t.get_height() // 2))

            s = _fmt(vals[key], step)
            t = font_med.render(s, True, _C_TEXT)
            screen.blit(t, (_VAL_X - t.get_width() // 2, cy - t.get_height() // 2))

    _draw_field(screen, font_med, "Port:", _port_rect(mode), port,
                focused == "port", mx, my)

    # Color hint for host/join
    if mode != "solo":
        color_label = "You play as White" if mode == "host" else "You play as Black"
        t = font_sml.render(color_label, True, (160, 160, 160))
        sr = _start_rect(mode)
        screen.blit(t, (_CX - t.get_width() // 2, sr.bottom + 8))

    # Start button
    sr  = _start_rect(mode)
    col = _C_START_H if sr.collidepoint(mx, my) else _C_START
    pygame.draw.rect(screen, col, sr, border_radius=8)
    t = font_big.render("START", True, _C_TEXT)
    screen.blit(t, (sr.centerx - t.get_width() // 2, sr.centery - t.get_height() // 2))


def _draw_field(screen, font, label: str, rect: pygame.Rect,
                text: str, focused: bool, mx: int, my: int) -> None:
    t = font.render(label, True, _C_TEXT)
    screen.blit(t, (_LABEL_X, rect.centery - t.get_height() // 2))
    col    = (70, 70, 100) if focused else _C_INPUT
    border = (110, 110, 160) if focused else (60, 60, 80)
    pygame.draw.rect(screen, col, rect, border_radius=4)
    pygame.draw.rect(screen, border, rect, 1, border_radius=4)
    t = font.render(text, True, _C_TEXT)
    screen.blit(t, (rect.x + 8, rect.centery - t.get_height() // 2))
