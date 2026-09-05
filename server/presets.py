"""
Tempo presets, server side.

The client picks a tempo and sends the numbers, which is fine for play: every
value is bounds-checked in params.py, and a room where both seats agreed to odd
settings is a room, not a cheat. Rating is the part that cannot take numbers on
trust. A rated game has to be a game everyone played under the same rules, so
the server needs to recognise the three tempos itself rather than believe a
label - "bullet" with a hand-edited cooldown would otherwise be a rated game on
settings nobody else can get.

This is a second copy of the table in web/src/presets.ts, which is the one with
the reasoning written down and the one to edit. tools/civ_parity.py asserts the
two agree, for the same reason it does for civilizations.
"""

MODES: tuple[str, ...] = ("bullet", "rapid", "slow")

# Column order matches MODES.
_TABLE: dict[str, tuple[float, float, float]] = {
    #                       bullet  rapid   slow
    "mana_refill_rate":     (0.35,  0.15,   0.075),
    "maximum_mana":         (5.0,   5.0,    5.0),
    "base_move_cost":       (1.0,   1.0,    1.0),
    "distance_cost":        (0.2,   0.2,    0.2),
    "preparation_period":   (0.35,  0.5,    0.65),
    "cooldown":             (0.9,   1.3,    1.7),
    "movement_speed":       (4.5,   2.0,    1.0),
    "movement_freedom_deg": (5.0,   5.0,    5.0),
    "diameter_piece":       (0.6,   0.6,    0.6),
}

PRESETS: dict[str, dict[str, float]] = {
    mode: {key: row[col] for key, row in _TABLE.items()}
    for col, mode in enumerate(MODES)
}


def tempo_name(p: dict | None) -> str | None:
    """Which preset these params are, or None for custom.

    None is not a failure: a custom room is a perfectly good game, it just
    cannot be rated, because the rating would be measuring the settings.
    """
    if not isinstance(p, dict):
        return None
    for mode, preset in PRESETS.items():
        if all(abs(p.get(key, float("nan")) - value) < 1e-6
               for key, value in preset.items()):
            return mode
    return None
