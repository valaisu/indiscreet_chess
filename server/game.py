import asyncio
import math

from . import params
from .pieces import Piece, PieceType, PieceState, initial_board
from shared.protocol import GAME_STATE, MOVE_REJECTED, GAME_OVER


class GameState:
    def __init__(self, solo: bool = False) -> None:
        self.solo = solo
        self.pieces: list[Piece] = initial_board()
        self.mana: dict[str, float] = {
            "white": params.MAXIMUM_MANA,
            "black": params.MAXIMUM_MANA,
        }
        self.tick: int = 0
        self.started: bool = False
        self.game_over: bool = False
        self.winner: str | None = None
        # Moves submitted by clients since the last tick.
        self._pending: list[dict] = []
        # En passant ghosts — populated in Part 4.
        self.ghosts: list[dict] = []

    # ------------------------------------------------------------------
    # Public interface (called from network handler)
    # ------------------------------------------------------------------

    def queue_move(self, piece_id: str, dest: tuple[float, float],
                   requesting_color: str) -> dict | None:
        """Validate and enqueue a move. Returns a rejection dict or None."""
        if not self.started:
            return _reject(piece_id, "game not started")

        piece = self._find(piece_id)
        if piece is None:
            return _reject(piece_id, "piece not found")

        if not self.solo and piece.owner != requesting_color:
            return _reject(piece_id, "not your piece")

        if piece.state != PieceState.IDLE:
            return _reject(piece_id, "piece not idle")

        dest_x, dest_y = dest
        dist = math.hypot(dest_x - piece.x, dest_y - piece.y)
        cost = params.BASE_MOVE_COST + params.DISTANCE_COST * dist
        mana_owner = piece.owner

        if self.mana[mana_owner] < cost:
            return _reject(piece_id, "insufficient mana")

        self.mana[mana_owner] -= cost
        self._pending.append({
            "piece_id": piece_id,
            "dest_x": dest_x,
            "dest_y": dest_y,
        })
        return None

    # ------------------------------------------------------------------
    # Game loop
    # ------------------------------------------------------------------

    async def run(self, broadcast_fn) -> None:
        self.started = True
        tick_dt = 1.0 / params.TICK_RATE
        loop = asyncio.get_event_loop()

        while not self.game_over:
            t0 = loop.time()
            self._tick(tick_dt)
            await broadcast_fn(self.to_dict())
            elapsed = loop.time() - t0
            await asyncio.sleep(max(0.0, tick_dt - elapsed))

        # Broadcast final state.
        await broadcast_fn(self.to_dict())

    def _tick(self, dt: float) -> None:
        self.tick += 1

        # 1. Regenerate mana.
        for owner in self.mana:
            self.mana[owner] = min(
                self.mana[owner] + params.MANA_REFILL_RATE * dt,
                params.MAXIMUM_MANA,
            )

        # 2. Apply pending moves (set pieces to PREPARATION).
        pending, self._pending = self._pending, []
        for move in pending:
            self._apply_move(move)

        # 3. Advance state machines (no collision detection until Part 3).
        for piece in self.pieces:
            piece.advance(dt)

        # 4. Check win condition.
        self._check_win()

    def _apply_move(self, move: dict) -> None:
        piece = self._find(move["piece_id"])
        if piece is None or piece.state != PieceState.IDLE:
            return
        piece.dest_x = move["dest_x"]
        piece.dest_y = move["dest_y"]
        piece.state = PieceState.PREPARATION
        piece.state_timer = params.PREPARATION_PERIOD

    def _check_win(self) -> None:
        kings_by_owner: dict[str, int] = {}
        for p in self.pieces:
            if p.type == PieceType.KING:
                kings_by_owner[p.owner] = kings_by_owner.get(p.owner, 0) + 1

        white_alive = kings_by_owner.get("white", 0) > 0
        black_alive = kings_by_owner.get("black", 0) > 0

        if not white_alive and not black_alive:
            self.game_over = True
            self.winner = "draw"
        elif not white_alive:
            self.game_over = True
            self.winner = "black"
        elif not black_alive:
            self.game_over = True
            self.winner = "white"

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        base = {
            "type": GAME_STATE,
            "tick": self.tick,
            "pieces": [p.to_dict() for p in self.pieces],
            "mana": {k: round(v, 3) for k, v in self.mana.items()},
            "ghosts": self.ghosts,
            "game_over": self.game_over,
            "winner": self.winner,
        }
        return base

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find(self, piece_id: str) -> Piece | None:
        for p in self.pieces:
            if p.id == piece_id:
                return p
        return None


def _reject(piece_id: str, reason: str) -> dict:
    return {"type": MOVE_REJECTED, "piece_id": piece_id, "reason": reason}
