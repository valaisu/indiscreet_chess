TICK_RATE: int = 20               # ticks per second
MANA_REFILL_RATE: float = 0.3    # mana per second
MAXIMUM_MANA: float = 5.0
BASE_MOVE_COST: float = 1.0
DISTANCE_COST: float = 0.2       # mana per board unit
MOVEMENT_FREEDOM_DEG: float = 5.0
DIAMETER_PIECE: float = 0.6      # in square units
SQUARE_SIDE: float = 1.0
PREPARATION_PERIOD: float = 0.5  # seconds
MOVEMENT_SPEED: float = 4.0      # board units per second
COOLDOWN: float = 0.8            # seconds
SERVER_HOST: str = "0.0.0.0"
SERVER_PORT: int = 8765

# --- Online play limits ---
# Params arrive over the wire from a public page, so they are untrusted input.
# Out-of-range values are rejected rather than clamped: the UI already
# constrains them, so anything outside these bounds means a tampered client
# and should be visible in the logs. TICK_RATE is deliberately absent — it
# stays server-side and is not client-settable.
LIMITS: dict[str, tuple[float, float]] = {
    "mana_refill_rate":     (0.01, 10.0),
    "maximum_mana":         (1.0, 50.0),
    "base_move_cost":       (0.0, 20.0),
    "distance_cost":        (0.0, 5.0),
    "preparation_period":   (0.0, 10.0),
    "movement_speed":       (0.5, 40.0),
    "cooldown":             (0.0, 10.0),
    "movement_freedom_deg": (0.0, 45.0),
    "diameter_piece":       (0.05, 1.5),
}

DEFAULT_PARAMS: dict[str, float] = {
    "mana_refill_rate":     MANA_REFILL_RATE,
    "maximum_mana":         MAXIMUM_MANA,
    "base_move_cost":       BASE_MOVE_COST,
    "distance_cost":        DISTANCE_COST,
    "preparation_period":   PREPARATION_PERIOD,
    "movement_speed":       MOVEMENT_SPEED,
    "cooldown":             COOLDOWN,
    "movement_freedom_deg": MOVEMENT_FREEDOM_DEG,
    "diameter_piece":       DIAMETER_PIECE,
}


def validate_params(d: object) -> str | None:
    """Return a rejection reason, or None if the param dict is acceptable."""
    if d is None:
        return None
    if not isinstance(d, dict):
        return "params must be an object"
    for key, value in d.items():
        if key not in LIMITS:
            return f"unknown param: {key}"
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{key} must be a number"
        if value != value or value in (float("inf"), float("-inf")):
            return f"{key} must be finite"
        lo, hi = LIMITS[key]
        if not (lo <= value <= hi):
            return f"{key} must be between {lo} and {hi}"
    return None
