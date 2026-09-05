"""
Game recording as an event log.

A GAME_STATE snapshot is 6.9 KB, and at 20 Hz a five minute game is 41 MB of
them. Storing that per game is not an option, so a recording is the *effects*
instead: the handful of moments where something actually changed. Everything
between two events is a straight line in time, which the player reconstructs
by running the same timers the server ran.

Effects, not inputs. Recording the moves and re-simulating would be smaller
still, but it would mean every stored game is only replayable by the exact
engine that recorded it: the day physics.py is touched to rebalance, every old
replay silently becomes a different game. An effects log says what happened,
so it survives the rules changing underneath it, needs no engine on the client,
and costs no server CPU to play back.

The recorder is a differ. It looks at the piece list once per tick and writes
down what is not the same as last tick, so it has no hooks inside the physics
and keeps working when the physics changes. The cost is one pass over ~32
pieces per tick.

The log is the whole truth, not one seat's view: it is written for a finished
game, where both civilizations are revealed anyway. Never serve the log of a
game still in progress - that would hand a player the opponent's hidden
preparation and destinations.
"""

from .pieces import Piece, PieceState, PieceType

# Bumped when the event format changes in a way an old player cannot read.
# Stored with every recording so a player can refuse one it does not
# understand instead of drawing it wrongly.
FORMAT = 1

# Event kinds. Single letters: these are written once per event into a column
# that holds thousands of them.
SPAWN   = "+"    # a piece exists (opening board, and en passant ghosts)
REMOVE  = "-"    # captured, or a ghost cleaned up
STATE   = "s"    # entered a new state; carries position, velocity, timer
PROMOTE = "y"    # changed type mid-flight, so it changed hitbox too
MANA    = "m"    # both pools, whenever a move was paid for
END     = "e"    # game over


def _piece_row(p: Piece) -> dict:
    return {
        "id": p.id,
        "ty": p.type.value,
        "o":  p.owner,
        "x":  p.x,
        "y":  p.y,
        "d":  p.diameter,
    }


class Recorder:
    """Watches one GameState and accumulates its event log."""

    def __init__(self, game) -> None:
        self.format = FORMAT
        self.events: list[dict] = []
        # Static for the whole game: what the two sides are playing with. The
        # player needs these to run its timers and regenerate mana, and they
        # are exactly the fields to_dict repeats in every snapshot.
        self.header: dict = {
            "format": FORMAT,
            "tick_rate": None,      # filled by start(), which knows the loop's dt
            "civs": dict(game.civs),
            "solo": game.solo,
            "max_mana":    {c: game._pp[c]["maximum_mana"] for c in ("white", "black")},
            "refill":      {c: game._pp[c]["mana_refill_rate"] for c in ("white", "black")},
            "freedom_deg": {c: game._pp[c]["movement_freedom_deg"] for c in ("white", "black")},
            "prep_period": {c: game._pp[c]["preparation_period"] for c in ("white", "black")},
            "cooldown":    {c: game._pp[c]["cooldown"] for c in ("white", "black")},
            "player_params": {
                c: {
                    "base_move_cost": game._pp[c]["base_move_cost"],
                    "distance_cost":  game._pp[c]["distance_cost"],
                }
                for c in ("white", "black")
            },
            "piece_params": {c: game._piece_pp[c] for c in ("white", "black")},
            "pieces": [_piece_row(p) for p in game.pieces],
            "mana": {c: game.mana[c] for c in ("white", "black")},
        }
        # Last seen shape of every live piece, keyed by id.
        self._prev: dict[str, tuple] = {p.id: self._shape(p) for p in game.pieces}
        self._mana: dict[str, float] = dict(game.mana)

    # -- the differ ---------------------------------------------------------

    @staticmethod
    def _shape(p: Piece) -> tuple:
        """Everything about a piece that a frame shows."""
        return (p.type.value, p.state.value, p.diameter, p.has_moved,
                p.x, p.y, p.dest_x, p.dest_y, p.vel_x, p.vel_y, p.state_timer)

    @staticmethod
    def _predict(was: tuple, dt: float) -> tuple:
        """What the player will compute for this tick on its own.

        This is the player's whole advance step, mirrored: a moving piece
        travels at its velocity and counts down to arrival, anything else just
        counts down. Keeping the two in step is the point - an event is only
        written when reality departs from this, so whatever this predicts
        correctly costs nothing to store.
        """
        (ty, st, d, hm, x, y, dx, dy, vx, vy, tm) = was
        if st == PieceState.MOVING.value:
            return (ty, st, d, hm, x + vx * dt, y + vy * dt, dx, dy, vx, vy, tm - dt)
        return (ty, st, d, hm, x, y, dx, dy, vx, vy, max(0.0, tm - dt))

    def observe(self, game, dt: float) -> None:
        """Record what the player could not have worked out for itself. Called
        once per tick, after everything else in the tick has run."""
        tick = game.tick
        live: dict[str, tuple] = {}

        for p in game.pieces:
            shape = self._shape(p)
            live[p.id] = shape
            was = self._prev.get(p.id)

            if was is None:
                self.events.append({"k": SPAWN, "t": tick, **_piece_row(p)})
                self.events.append(self._state_event(tick, p))
                continue

            if was[0] != shape[0] or was[2] != shape[2]:
                # Promotion. It changes the hitbox as well as the glyph, and it
                # can happen mid-flight, so it is its own event rather than a
                # field on a state change that may not be happening this tick.
                self.events.append({"k": PROMOTE, "t": tick, "id": p.id,
                                    "ty": shape[0], "d": p.diameter})

            # Everything else: one comparison against the prediction, so a
            # field the physics rewrites without changing the state is caught
            # like any other. _continue_after_capture does exactly that - a
            # piece that takes something and carries on keeps its state while
            # its destination and arrival time are both rewritten - and a
            # differ watching only the state walked straight past it.
            pred = self._predict(was, dt)
            if (pred[1], pred[3:]) != (shape[1], shape[3:]):
                self.events.append(self._state_event(tick, p))

        for gone in self._prev.keys() - live.keys():
            self.events.append({"k": REMOVE, "t": tick, "id": gone})

        self._prev = live
        self._record_mana(tick, game)

    def _state_event(self, tick: int, p: Piece) -> dict:
        """A correction: this piece is not where running it forward would have
        put it. Everything the player needs to carry on from here.

        Velocity is recorded rather than derived. A castling rook's is synced
        to the king's travel time instead of its own speed, and a move cut
        short by a collision ends somewhere no formula predicts.

        Nothing here is rounded. The player advances a moving piece by
        vel * dt every tick from these numbers, so a velocity trimmed to four
        decimals compounds: over a long flight it drifts far enough to change
        the position a viewer is shown. Rounding is for the frames the player
        emits, which is where the wire format asks for it, not for the values
        its arithmetic starts from."""
        return {
            "k":  STATE,
            "t":  tick,
            "id": p.id,
            "st": p.state.value,
            "x":  p.x,
            "y":  p.y,
            "dx": p.dest_x,
            "dy": p.dest_y,
            "vx": p.vel_x,
            "vy": p.vel_y,
            "tm": p.state_timer,
            "hm": p.has_moved,
        }

    def _record_mana(self, tick: int, game) -> None:
        """Mana is spent between ticks, in queue_move, and regenerates inside
        the tick. Regeneration the player can run itself; spending it cannot
        predict, so a pool that fell below where refill alone would have put it
        is written down."""
        spent = False
        for c in ("white", "black"):
            if game.mana[c] < self._mana[c] - 1e-9:
                spent = True
            self._mana[c] = game.mana[c]
        if spent:
            self.events.append({"k": MANA, "t": tick,
                                "white": game.mana["white"],
                                "black": game.mana["black"]})

    # -- lifecycle ----------------------------------------------------------

    def start(self, tick_rate: int) -> None:
        self.header["tick_rate"] = tick_rate

    def finish(self, game) -> None:
        self.events.append({"k": END, "t": game.tick,
                            "winner": game.winner or "draw"})

    def to_dict(self) -> dict:
        return {"header": self.header, "events": self.events}
