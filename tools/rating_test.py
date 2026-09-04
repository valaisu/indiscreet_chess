"""
Elo arithmetic, and which games it is allowed to measure.

    PYTHONPATH=. python3 tools/rating_test.py
"""

import sys

from server import params, presets, rating

FAILS: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want if not isinstance(want, float) else abs(got - want) < 1e-9
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")


def main() -> int:
    # Equal ratings: an even game.
    check("equal ratings expect 0.5", rating.expected(1200.0, 1200.0), 0.5)
    # The textbook figure: 400 points of advantage is 10 to 1.
    check("400 points ahead expects 10/11",
          round(rating.expected(1600.0, 1200.0), 6), round(10 / 11, 6))

    # A win between equals moves both by half of K, in opposite directions.
    w, b = rating.update(1200.0, 1200.0, "white", 0, 0)
    check("even win moves white up by K/2", w, 1200.0 + 16.0)
    check("even win moves black down by K/2", b, 1200.0 - 16.0)

    # Zero sum, but only while both sides carry the same K.
    check("equal K is zero sum", round((w - 1200.0) + (b - 1200.0), 9), 0.0)

    # A draw between equals changes nothing.
    w, b = rating.update(1200.0, 1200.0, "draw", 0, 0)
    check("even draw leaves white", w, 1200.0)
    check("even draw leaves black", b, 1200.0)

    # A settled player moves half as far as a provisional one.
    w, _ = rating.update(1200.0, 1200.0, "white", rating.PROVISIONAL_GAMES, 0)
    check("settled winner moves by K_SETTLED/2", w, 1200.0 + 8.0)
    check("K switches at the boundary",
          (rating.k_factor(29), rating.k_factor(30)), (32.0, 16.0))

    # Beating someone far below you is worth almost nothing; losing to them hurts.
    up, _ = rating.update(1600.0, 1200.0, "white", 50, 50)
    down, _ = rating.update(1600.0, 1200.0, "black", 50, 50)
    check("favourite gains little", round(up - 1600.0, 2), 1.45)
    check("favourite loses a lot", round(down - 1600.0, 2), -14.55)

    # --- which games count ------------------------------------------------
    rapid = presets.PRESETS["rapid"]
    check("a standard signed-in game is rated",
          rating.rated_reason(False, rapid, None, "u1", "u2"), None)
    check("defaults passed explicitly are still rated",
          rating.rated_reason(False, rapid, dict(params.VIEW_DEFAULTS), "u1", "u2"), None)
    check("solo is not rated",
          rating.rated_reason(True, rapid, None, "u1", "u2"),
          "solo games are not rated")
    check("an anonymous seat is not rated",
          rating.rated_reason(False, rapid, None, "u1", None),
          "both players must be signed in")
    check("a custom tempo is not rated",
          rating.rated_reason(False, {**rapid, "cooldown": 1.25}, None, "u1", "u2"),
          "only the standard tempos are rated")
    check("one account on both sides is not rated",
          rating.rated_reason(False, rapid, None, "u1", "u1"),
          "a game against yourself is not rated")
    check("altered visibility is not rated",
          rating.rated_reason(False, rapid, {"enemy_dest": True}, "u1", "u2"),
          "only the default visibility settings are rated")
    for mode in presets.MODES:
        check(f"{mode} is a recognised tempo",
              presets.tempo_name(presets.PRESETS[mode]), mode)
    check("custom params have no tempo name",
          presets.tempo_name({**rapid, "movement_speed": 2.5}), None)

    if FAILS:
        print(f"\nFAIL: {len(FAILS)} check(s)")
        for f in FAILS:
            print(f"  {f}")
        return 1
    print("\nPASS: Elo arithmetic and the rated-game rule")
    return 0


if __name__ == "__main__":
    sys.exit(main())
