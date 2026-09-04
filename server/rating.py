"""
Elo rating, and the rule for which games get one.

Standard Elo: one formula, well understood, and nobody argues about it. What
needs deciding is not the arithmetic but which games it is allowed to measure,
and that is `rated_reason` below.

Kept per tempo, three ratings per player. A civilization is a balanced side
choice rather than a different game, so civ and non-civ games share one rating
within a mode.
"""

from . import params, presets

START = 1200.0

# Higher while a rating is still finding its level, lower once it has settled.
PROVISIONAL_GAMES = 30
K_PROVISIONAL = 32.0
K_SETTLED = 16.0


def k_factor(games_played: int) -> float:
    return K_PROVISIONAL if games_played < PROVISIONAL_GAMES else K_SETTLED


def expected(rating: float, opponent: float) -> float:
    """Probability that `rating` beats `opponent`, as Elo defines it."""
    return 1.0 / (1.0 + 10.0 ** ((opponent - rating) / 400.0))


def update(white: float, black: float, winner: str,
           white_games: int, black_games: int) -> tuple[float, float]:
    """Both new ratings after one game. `winner` is "white", "black" or "draw".

    The two sides can move by different amounts, because a provisional player
    moves further than a settled one. That is deliberate and is why this
    returns both rather than one delta applied twice.
    """
    score = {"white": 1.0, "black": 0.0, "draw": 0.5}[winner]
    new_white = white + k_factor(white_games) * (score - expected(white, black))
    new_black = black + k_factor(black_games) * ((1.0 - score) - expected(black, white))
    return new_white, new_black


def rated_reason(solo: bool, base_params: dict | None, view: dict | None,
                 white_user_id: str | None, black_user_id: str | None) -> str | None:
    """Why this game cannot be rated, or None if it can.

    A reason rather than a bool so the lobby can say which condition is
    missing. The player should know before the game starts, not after.

    Takes the account ids, not two booleans, because the cheapest rating farm
    there is is one person on both sides: sign in on two tabs, resign
    repeatedly, and feed a second account. That is invisible to a check that
    only asks whether each seat is signed in.
    """
    if solo:
        return "solo games are not rated"
    if not (white_user_id and black_user_id):
        return "both players must be signed in"
    if white_user_id == black_user_id:
        return "a game against yourself is not rated"
    if presets.tempo_name(base_params) is None:
        # Rating a custom tempo would measure the settings, not the players.
        return "only the standard tempos are rated"
    merged = params.build_view(view)
    if merged != params.VIEW_DEFAULTS:
        # Visibility changes what the game is. A room where you can see the
        # opponent's destinations is a different skill from one where you
        # cannot, and one rating cannot span both.
        return "only the default visibility settings are rated"
    return None
