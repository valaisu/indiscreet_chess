"""
Civilizations, resolved server side.

A mirror of the tables in web/src/civs.ts, and deliberately a dumb one: the
client owns the presentation (the cards, the percentages, the titles) and this
owns the only copy that a game is ever played with. A client used to send the
finished numbers and the server merely bounds-checked them, which meant any
patched client could pick "roman" and play with a tenth of the cooldown. Now it
sends a name, and the numbers are derived here from the room's own tempo.

Two copies of a table drift, so tools/civ_parity.py runs the TypeScript one and
asserts both resolve every civilization to the same params. Edit civs.ts first;
it is the one with the reasoning written down.
"""

import hashlib
import json
import math

from . import params
from .pieces import start_overlap_reason

CIV_NAMES: tuple[str, ...] = (
    "hun", "roman", "greek", "persian",
    "egyptian", "norse", "swiss", "byzantine",
)

# Percent change from the base tempo. Column order matches CIV_NAMES.
TABLE: dict[str, tuple[float, ...]] = {
    #                       hun  roman  greek  persia  egypt  norse  swiss  byzant
    "mana_refill_rate":     ( -6,     0,     0,      0,     0,    -6,     6,      0),
    "maximum_mana":         (  0,     0,    -5,      0,    20,    -8,     5,      5),
    "base_move_cost":       (  0,   -10,     0,     10,     0,     0,     0,     10),
    "distance_cost":        (  0,     0,    10,    -24,     8,     0,     0,      0),
    "preparation_period":   (  0,     0,   -10,      0,    24,   -20,    10,   14.8),
    "cooldown":             ( 13,   -10,     0,   13.5,     0,     0,     0,    -20),
    "movement_speed":       ( 20,   -26,     0,      0,     0,    10, -20.5,      0),
    "movement_freedom_deg": ( 15,   -15,  34.2,      0,     0,     0,     0,      0),
    "diameter_piece":       (  0,     0,     0,      0,    10,    -6,     0,      0),
}

# [piece type, param, percent] per civ. Not every civ has one.
PIECE_TABLE: dict[str, list[tuple[str, str, float]]] = {
    "hun":       [("knight", "cooldown",           -15)],
    "roman":     [("pawn",   "base_move_cost",     -10)],
    "greek":     [("rook",   "movement_speed",     -20),
                  ("pawn",   "diameter_piece",     +20)],
    "persian":   [("rook",   "distance_cost",      -20)],
    "egyptian":  [("king",   "base_move_cost",     -25)],
    "norse":     [],
    "swiss":     [("pawn",   "cooldown",           -15),
                  ("knight", "preparation_period", +20)],
    "byzantine": [("king",   "preparation_period", -30),
                  ("king",   "diameter_piece",     -30)],
}

# Percent changes per civ, zeros dropped. Dropping them is not tidiness: a zero
# row must leave the base value untouched, and applying a factor of 1.0 would
# still round it to three decimals.
PERCENTS: dict[str, dict[str, float]] = {
    civ: {key: row[col] for key, row in TABLE.items() if row[col] != 0}
    for col, civ in enumerate(CIV_NAMES)
}


def table_fingerprint() -> str:
    """A short id for the civilization table as it stands right now.

    Stored with every finished game, so balance figures can be grouped by the
    table they were played under. Without it, a win rate silently averages
    games from before and after a rebalance, and the number that says whether
    the rebalance worked is the one thing it cannot tell you.

    Derived rather than hand-bumped: a version constant that has to be
    remembered will be forgotten in exactly the commit that changes a
    percentage.
    """
    blob = json.dumps([PERCENTS, PIECE_TABLE], sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def _round3(value: float) -> float:
    """Three decimals, rounding halves up - what Math.round does in the client.
    Python's round() rounds halves to even, which disagrees on exact ties."""
    return math.floor(value * 1000.0 + 0.5) / 1000.0


def resolve(base: dict | None, civ: str | None) -> dict:
    """The params a seat actually plays with: the room's tempo with one
    civilization applied. An unknown or absent civ leaves the tempo alone.

    Only keys the base already names are modified, so a tempo that does not
    mention a param leaves it at the server default rather than inventing one.
    """
    out = dict(base or {})
    for key, pct in PERCENTS.get(civ or "", {}).items():
        if key in out:
            out[key] = _round3(out[key] * (1.0 + pct / 100.0))

    pieces: dict[str, dict[str, float]] = {}
    for piece, key, pct in PIECE_TABLE.get(civ or "", []):
        if key in out:
            pieces.setdefault(piece, {})[key] = _round3(out[key] * (1.0 + pct / 100.0))
    if pieces:
        out["pieces"] = pieces
    return out


def resolve_checked(base: dict | None, civ: str | None) -> tuple[dict, str | None]:
    """resolve(), plus the checks every param dict has to pass. A civilization
    multiplies the tempo, so an extreme custom tempo can push one over a limit
    or start the pieces touching - the pick is refused rather than clamped, and
    the player is told which one it was."""
    resolved = resolve(base, civ)
    reason = params.validate_params(resolved) or start_overlap_reason(resolved)
    if reason:
        return resolved, f"{civ or 'this tempo'} cannot be played here: {reason}"
    return resolved, None
