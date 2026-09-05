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
    # base_params is per seat: both sides of an even room hold the same dict.
    rapid_row = presets.PRESETS["rapid"]
    def both(p: dict) -> dict:
        return {"white": p, "black": p}
    rapid = both(rapid_row)
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
          rating.rated_reason(False, both({**rapid_row, "cooldown": 1.25}),
                              None, "u1", "u2"),
          "only the standard tempos are rated")
    check("one account on both sides is not rated",
          rating.rated_reason(False, rapid, None, "u1", "u1"),
          "a game against yourself is not rated")
    check("altered visibility is not rated",
          rating.rated_reason(False, rapid, {"enemy_dest": True}, "u1", "u2"),
          "only the default visibility settings are rated")

    # A balanced room is deliberately uneven, so its result measures the
    # handicap. Checked before the tempo, because both sides can be standard
    # presets and the room still not be a fair game.
    faster_black = {"white": rapid_row,
                    "black": {**rapid_row, "mana_refill_rate": 0.25}}
    check("a balanced room is not rated",
          rating.rated_reason(False, faster_black, None, "u1", "u2"),
          "balanced games are not rated")
    check("two different standard tempos are not rated either",
          rating.rated_reason(False,
                              {"white": presets.PRESETS["bullet"],
                               "black": presets.PRESETS["slow"]},
                              None, "u1", "u2"),
          "balanced games are not rated")
    check("an equal room built from two separate dicts is still rated",
          rating.rated_reason(False,
                              {"white": dict(rapid_row), "black": dict(rapid_row)},
                              None, "u1", "u2"),
          None)
    # The host can simply say no. Checked first, because it is the one reason
    # that is a choice rather than a property of the room, and a player who
    # asked for an unrated game should be told that, not something else.
    check("an unrated room is not rated",
          rating.rated_reason(False, rapid, None, "u1", "u2", unrated=True),
          "the host chose an unrated game")
    check("unrated outranks the other reasons",
          rating.rated_reason(True, faster_black, {"enemy_dest": True},
                              "u1", None, unrated=True),
          "the host chose an unrated game")

    # settings_reason answers the same question about the room alone. An open
    # room has one player in it at most, so asking the full question there
    # would label every waiting room "both players must be signed in".
    check("an empty standard room could host a rated game",
          rating.settings_reason(False, rapid, None), None)
    check("the settings answer ignores who is sitting there",
          rating.settings_reason(False, faster_black, None),
          "balanced games are not rated")
    check("the settings answer honours the unrated flag",
          rating.settings_reason(False, rapid, None, unrated=True),
          "the host chose an unrated game")

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
