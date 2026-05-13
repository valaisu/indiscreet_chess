"""Start menu: mode selection and parameter configuration."""
import sys
import pygame

from client.renderer import (
    WIN_W, WIN_H,
    draw_fullscreen_btn, fullscreen_btn_rect,
)

# ── Colors ────────────────────────────────────────────────────────────────────
_C_BG      = ( 30,  30,  30)
_C_TEXT    = (220, 220, 220)
_C_ACTIVE  = ( 70, 130, 200)
_C_BTN     = ( 55,  55,  75)
_C_BTN_H   = ( 80,  80, 110)
_C_SEP     = ( 70,  70,  70)
_C_INPUT   = ( 45,  45,  60)
_C_START   = ( 60, 120,  60)
_C_START_H = ( 90, 160,  90)

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

# Display-only settings (client-side, not sent to server)
_DISPLAY = [
    ("Own timers",  "show_own_timers"),
    ("Opp timers",  "show_opp_timers"),
]

DISPLAY_DEFAULTS: dict = {
    "show_own_timers": True,
    "show_opp_timers": True,
}


# ── Dynamic layout ────────────────────────────────────────────────────────────

def _make_layout(win_w: int, win_h: int) -> dict:
    scale = min(win_w / WIN_W, win_h / WIN_H)
    off_x = (win_w - int(WIN_W * scale)) // 2
    off_y = (win_h - int(WIN_H * scale)) // 2

    def s(v: float) -> int:
        return max(1, int(v * scale))

    return dict(
        scale   = scale,
        off_x   = off_x,
        off_y   = off_y,
        win_w   = win_w,
        win_h   = win_h,
        cx      = off_x + s(400),
        title_y = off_y + s(42),
        mode_y  = off_y + s(100),
        sep_y   = off_y + s(150),
        body_y  = off_y + s(168),
        row_h   = s(36),
        btn_w   = max(4, s(28)),
        btn_h   = max(4, s(28)),
        label_x = off_x + s(85),
        minus_x = off_x + s(508),
        val_x   = off_x + s(578),
        plus_x  = off_x + s(638),
        f_big   = max(12, s(34)),
        f_med   = max(8,  s(18)),
        f_sml   = max(6,  s(14)),
        s       = s,
    )


def _display_top_y(L: dict, mode: str) -> int:
    """Y position of the first display-toggle row."""
    base = L['s'](52) if mode == "join" else len(_PARAMS) * L['row_h'] + L['s'](8)
    return L['body_y'] + base


def _port_y(L: dict, mode: str) -> int:
    return _display_top_y(L, mode) + len(_DISPLAY) * L['row_h'] + L['s'](16)


def _start_y(L: dict, mode: str) -> int:
    return _port_y(L, mode) + L['s'](52)


def _toggle_rect(L: dict, mode: str, idx: int) -> pygame.Rect:
    ry  = _display_top_y(L, mode) + idx * L['row_h']
    off = (L['row_h'] - L['btn_h']) // 2
    tw  = max(L['btn_w'] + L['s'](24), L['s'](52))
    return pygame.Rect(L['plus_x'], ry + off, tw, L['btn_h'])


def _mode_rect(L: dict, i: int) -> pygame.Rect:
    s = L['s']
    w, h, gap = s(118), s(38), s(18)
    x = L['cx'] - (3 * w + 2 * gap) // 2 + i * (w + gap)
    return pygame.Rect(x, L['mode_y'], w, h)


def _ip_rect(L: dict) -> pygame.Rect:
    s = L['s']
    return pygame.Rect(L['off_x'] + s(240), L['body_y'], s(380), s(34))


def _port_rect(L: dict, mode: str) -> pygame.Rect:
    s = L['s']
    return pygame.Rect(L['off_x'] + s(240), _port_y(L, mode), s(120), s(34))


def _start_rect(L: dict, mode: str) -> pygame.Rect:
    s = L['s']
    return pygame.Rect(L['cx'] - s(100), _start_y(L, mode), s(200), s(48))


def _fmt(val: float, step: float) -> str:
    if step >= 1.0: return f"{val:.0f}"
    if step >= 0.1: return f"{val:.1f}"
    return f"{val:.2f}"


# ── Public entry point ────────────────────────────────────────────────────────

def run_menu(screen: pygame.Surface) -> dict:
    """
    Blocking menu loop.  Returns:
      {"mode": "solo"|"host"|"join", "host_ip": str, "port": int, "params": dict}
    """
    fam = "dejavusans,arial,sans-serif"

    mode         = "solo"
    vals         = dict(DEFAULTS)
    display_vals = dict(DISPLAY_DEFAULTS)
    ip           = "localhost"
    port         = "8765"
    focused      = None

    cur_size = (0, 0)
    L        = {}
    fonts    = {}

    def refresh(w: int, h: int) -> None:
        nonlocal L, cur_size
        L        = _make_layout(w, h)
        cur_size = (w, h)
        fonts['big'] = pygame.font.SysFont(fam, L['f_big'])
        fonts['med'] = pygame.font.SysFont(fam, L['f_med'])
        fonts['sml'] = pygame.font.SysFont(fam, L['f_sml'])

    refresh(*screen.get_size())

    clock = pygame.time.Clock()
    while True:
        clock.tick(60)

        win_size = screen.get_size()
        if win_size != cur_size:
            refresh(*win_size)

        mx, my = pygame.mouse.get_pos()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_F11:
                    pygame.display.toggle_fullscreen()
                elif focused == "ip":
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
                w, h = screen.get_size()
                if fullscreen_btn_rect(w, h).collidepoint(mx, my):
                    pygame.display.toggle_fullscreen()
                else:
                    focused = None
                    action  = _click(mx, my, mode, L)
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
                            "display": display_vals,
                        }
                    elif action and action.startswith("toggle_"):
                        key = action[len("toggle_"):]
                        display_vals[key] = not display_vals.get(key, True)
                    elif action and action[0] in "+-":
                        _adjust(vals, action[1:], 1 if action[0] == "+" else -1)

        _draw(screen, fonts, mode, vals, display_vals, ip, port, focused, mx, my, L)
        draw_fullscreen_btn(screen, bool(screen.get_flags() & pygame.FULLSCREEN), mx, my)
        pygame.display.flip()


# ── Input handling ────────────────────────────────────────────────────────────

def _click(mx: int, my: int, mode: str, L: dict) -> str | None:
    for i, m in enumerate(("solo", "host", "join")):
        if _mode_rect(L, i).collidepoint(mx, my):
            return m

    if mode == "join":
        if _ip_rect(L).collidepoint(mx, my):
            return "focus_ip"
    else:
        row_h   = L['row_h']
        btn_w   = L['btn_w']
        btn_h   = L['btn_h']
        minus_x = L['minus_x']
        plus_x  = L['plus_x']
        body_y  = L['body_y']
        for idx, (_, key, *_rest) in enumerate(_PARAMS):
            ry  = body_y + idx * row_h
            off = (row_h - btn_h) // 2
            if pygame.Rect(minus_x, ry + off, btn_w, btn_h).collidepoint(mx, my):
                return f"-{key}"
            if pygame.Rect(plus_x,  ry + off, btn_w, btn_h).collidepoint(mx, my):
                return f"+{key}"

    for idx, (_, key) in enumerate(_DISPLAY):
        if _toggle_rect(L, mode, idx).collidepoint(mx, my):
            return f"toggle_{key}"

    if _port_rect(L, mode).collidepoint(mx, my):
        return "focus_port"
    if _start_rect(L, mode).collidepoint(mx, my):
        return "start"
    return None


def _adjust(vals: dict, key: str, direction: int) -> None:
    for _, k, lo, hi, step in _PARAMS:
        if k == key:
            vals[k] = round(max(lo, min(hi, vals[k] + direction * step)), 6)
            return


# ── Drawing ───────────────────────────────────────────────────────────────────

def _draw(screen, fonts, mode, vals, display_vals, ip, port, focused, mx, my, L: dict) -> None:
    screen.fill(_C_BG)

    cx      = L['cx']
    label_x = L['label_x']
    s       = L['s']

    # Title
    t = fonts['big'].render("Indiscreet Chess", True, (220, 220, 180))
    screen.blit(t, (cx - t.get_width() // 2, L['title_y']))

    # Mode buttons
    for i, (lbl, m) in enumerate((("Solo", "solo"), ("Host", "host"), ("Join", "join"))):
        r   = _mode_rect(L, i)
        col = _C_ACTIVE if m == mode else (_C_BTN_H if r.collidepoint(mx, my) else _C_BTN)
        pygame.draw.rect(screen, col, r, border_radius=6)
        t = fonts['med'].render(lbl, True, _C_TEXT)
        screen.blit(t, (r.centerx - t.get_width() // 2, r.centery - t.get_height() // 2))

    # Separator
    margin = L['off_x'] + s(80)
    pygame.draw.line(screen, _C_SEP, (margin, L['sep_y']), (L['win_w'] - margin, L['sep_y']))

    if mode == "join":
        _draw_field(screen, fonts['med'], "Server IP:",
                    _ip_rect(L), ip, focused == "ip", mx, my, label_x)
    else:
        row_h   = L['row_h']
        btn_w   = L['btn_w']
        btn_h   = L['btn_h']
        minus_x = L['minus_x']
        plus_x  = L['plus_x']
        val_x   = L['val_x']
        body_y  = L['body_y']

        for idx, (label, key, _lo, _hi, step) in enumerate(_PARAMS):
            ry  = body_y + idx * row_h
            cy  = ry + row_h // 2
            off = (row_h - btn_h) // 2

            t = fonts['sml'].render(label, True, _C_TEXT)
            screen.blit(t, (label_x, cy - t.get_height() // 2))

            for bx_btn, lbl in ((minus_x, "−"), (plus_x, "+")):
                r   = pygame.Rect(bx_btn, ry + off, btn_w, btn_h)
                col = _C_BTN_H if r.collidepoint(mx, my) else _C_BTN
                pygame.draw.rect(screen, col, r, border_radius=4)
                t = fonts['med'].render(lbl, True, _C_TEXT)
                screen.blit(t, (r.centerx - t.get_width() // 2,
                                r.centery - t.get_height() // 2))

            sv = _fmt(vals[key], step)
            t  = fonts['med'].render(sv, True, _C_TEXT)
            screen.blit(t, (val_x - t.get_width() // 2, cy - t.get_height() // 2))

    # Display-toggle section
    row_h   = L['row_h']
    btn_h   = L['btn_h']
    disp_y  = _display_top_y(L, mode)
    sep_x1  = L['off_x'] + L['s'](80)
    sep_x2  = L['win_w'] - sep_x1
    pygame.draw.line(screen, _C_SEP,
                     (sep_x1, disp_y - L['s'](6)), (sep_x2, disp_y - L['s'](6)))
    for idx, (label, key) in enumerate(_DISPLAY):
        ry   = disp_y + idx * row_h
        cy2  = ry + row_h // 2
        off  = (row_h - btn_h) // 2
        t    = fonts['sml'].render(label, True, _C_TEXT)
        screen.blit(t, (label_x, cy2 - t.get_height() // 2))
        r    = _toggle_rect(L, mode, idx)
        val  = display_vals.get(key, True)
        base = (60, 140, 60) if val else _C_BTN
        col  = ((90, 180, 90) if val else _C_BTN_H) if r.collidepoint(mx, my) else base
        pygame.draw.rect(screen, col, r, border_radius=4)
        lbl  = fonts['sml'].render("ON" if val else "OFF", True, _C_TEXT)
        screen.blit(lbl, (r.centerx - lbl.get_width() // 2,
                          r.centery - lbl.get_height() // 2))

    _draw_field(screen, fonts['med'], "Port:",
                _port_rect(L, mode), port, focused == "port", mx, my, label_x)

    if mode != "solo":
        color_label = "You play as White" if mode == "host" else "You play as Black"
        t  = fonts['sml'].render(color_label, True, (160, 160, 160))
        sr = _start_rect(L, mode)
        screen.blit(t, (cx - t.get_width() // 2, sr.bottom + s(8)))

    sr  = _start_rect(L, mode)
    col = _C_START_H if sr.collidepoint(mx, my) else _C_START
    pygame.draw.rect(screen, col, sr, border_radius=8)
    t = fonts['big'].render("START", True, _C_TEXT)
    screen.blit(t, (sr.centerx - t.get_width() // 2, sr.centery - t.get_height() // 2))


def _draw_field(screen, font, label: str, rect: pygame.Rect,
                text: str, focused: bool, mx: int, my: int,
                label_x: int) -> None:
    t = font.render(label, True, _C_TEXT)
    screen.blit(t, (label_x, rect.centery - t.get_height() // 2))
    col    = (70, 70, 100) if focused else _C_INPUT
    border = (110, 110, 160) if focused else (60, 60, 80)
    pygame.draw.rect(screen, col,    rect, border_radius=4)
    pygame.draw.rect(screen, border, rect, 1, border_radius=4)
    t = font.render(text, True, _C_TEXT)
    screen.blit(t, (rect.x + 8, rect.centery - t.get_height() // 2))
