import copy


def interpolate(state: dict, elapsed: float) -> dict:
    """
    Return a shallow-copied state with MOVING piece positions advanced by
    `elapsed` seconds using the velocity reported in the last server tick.
    Clamped so pieces never overshoot their destination.
    """
    result = copy.deepcopy(state)
    for piece in result["pieces"]:
        if piece["state"] == "moving":
            dt = min(elapsed, piece["state_timer"])
            piece["x"] += piece.get("vel_x", 0.0) * dt
            piece["y"] += piece.get("vel_y", 0.0) * dt
        if piece["state"] in ("moving", "preparation", "cooldown"):
            piece["state_timer"] = max(0.0, piece["state_timer"] - elapsed)
    return result
