"""
The personal settings that follow an account.

Mirrors DEFAULTS in web/src/settings.ts: the same five keys with the same
meanings. This half exists only to say what may be stored, because the object
arrives from a client, goes into the database, and comes back out to every
browser that account signs in on.

A stored object holds only the keys the player changed while signed in. An
absent key is not "the default", it is "no opinion", and the device's own value
stands. So this validates a subset and never fills anything in.
"""

import math

MOVE_MODES = ("both", "click", "drag")

# The board is 8 squares. A longer threshold than that is a drag that can never
# be completed, which is a setting nobody wants carried to their other devices.
MAX_DRAG = 8.0

# A binding is a KeyboardEvent.key: one character ("q") or a name ("Escape").
MAX_KEY = 20


def clean(raw: object) -> dict:
    """The storable part of a settings object from a client.

    Unknown keys, wrong types and out-of-range numbers are dropped rather than
    rejected: a newer client sending a sixth setting to an older server should
    lose that one setting, not the whole save.

    isfinite because json.loads accepts NaN and Infinity, and a NaN threshold
    compares false against every drag, so every drag would become a click.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key, value in raw.items():
        if key == "moveMode":
            if value in MOVE_MODES:
                out[key] = value
        elif key == "dragThreshold":
            # bool before number: True is an int in Python and would store 1.0.
            if (isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) and 0.0 <= value <= MAX_DRAG):
                out[key] = float(value)
        elif key == "showHints":
            if isinstance(value, bool):
                out[key] = value
        elif key in ("preciseKey", "unselectKey"):
            if isinstance(value, str) and 0 < len(value) <= MAX_KEY:
                out[key] = value
    return out
