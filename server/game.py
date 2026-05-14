import asyncio
import math

from . import params, physics
from .pieces import Piece, PieceType, PieceState, initial_board
from .rules import validate_move
from shared.protocol import GAME_STATE, MOVE_REJECTED, GAME_OVER


def _build_pp(overrides: dict | None) -> dict:
    """Merge per-player overrides with global param defaults."""
    o = overrides or {}
    return {
        "mana_refill_rate":     o.get("mana_refill_rate",    params.MANA_REFILL_RATE),
        "maximum_mana":         o.get("maximum_mana",        params.MAXIMUM_MANA),
        "base_move_cost":       o.get("base_move_cost",      params.BASE_MOVE_COST),
        "distance_cost":        o.get("distance_cost",       params.DISTANCE_COST),
        "preparation_period":   o.get("preparation_period",  params.PREPARATION_PERIOD),
        "movement_speed":       o.get("movement_speed",      params.MOVEMENT_SPEED),
        "cooldown":             o.get("cooldown",            params.COOLDOWN),
        "movement_freedom_deg": o.get("movement_freedom_deg",params.MOVEMENT_FREEDOM_DEG),
        "diameter_piece":       o.get("diameter_piece",      params.DIAMETER_PIECE),
    }


class GameState:
    def __init__(self, solo: bool = False,
                 params_white: dict | None = None,
                 params_black: dict | None = None) -> None:
        self.solo = solo
        self.pieces: list[Piece] = initial_board()
        self._pp: dict[str, dict] = {
            "white": _build_pp(params_white),
            "black": _build_pp(params_black),
        }
        for piece in self.pieces:
            piece.diameter = self._pp[piece.owner]["diameter_piece"]
        self.mana: dict[str, float] = {
            "white": self._pp["white"]["maximum_mana"] * 0.8,
            "black": self._pp["black"]["maximum_mana"] * 0.8,
        }
        self.tick: int = 0
        self.started: bool = False
        self.countdown: int | None = None
        self.game_over: bool = False
        self.winner: str | None = None
        self._pending: list[dict] = []
        # ghost_id → {"pawn_id": str, "window_closed": bool}
        # window_closed becomes True once the opponent's first move after the
        # pawn finishes has been checked (either targeting ghost or not).
        self._ghost_map: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def queue_move(self, piece_id: str, dest: tuple[float, float],
                   requesting_color: str) -> dict | None:
        """Validate and enqueue a move. Returns a rejection dict or None."""
        if not self.started:
            return _reject(piece_id, "game not started")

        piece = self._find(piece_id)
        if piece is None:
            return _reject(piece_id, "piece not found")

        if piece.type == PieceType.GHOST:
            return _reject(piece_id, "cannot move a ghost")

        if not self.solo and piece.owner != requesting_color:
            return _reject(piece_id, "not your piece")

        if piece.state != PieceState.IDLE:
            return _reject(piece_id, "piece not idle")

        dest_x, dest_y = dest
        pp = self._pp[piece.owner]

        reason = validate_move(piece, dest_x, dest_y, self.pieces, pp["movement_freedom_deg"])
        if reason:
            return _reject(piece_id, reason)

        dist = math.hypot(dest_x - piece.x, dest_y - piece.y)
        cost = pp["base_move_cost"] + pp["distance_cost"] * dist
        mana_owner = piece.owner

        if self.mana[mana_owner] < cost:
            return _reject(piece_id, "insufficient mana")

        self.mana[mana_owner] -= cost

        # En passant ghost expiry: opponent's move might close the window.
        self._expire_ghosts(piece, dest_x, dest_y)

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
        tick_dt = 1.0 / params.TICK_RATE
        loop = asyncio.get_event_loop()

        for n in (3, 2, 1, 0):
            self.countdown = n
            await broadcast_fn(self.to_dict())
            await asyncio.sleep(1.0 if n > 0 else 0.5)

        self.countdown = None
        self.started = True

        while not self.game_over:
            t0 = loop.time()
            self._tick(tick_dt)
            await broadcast_fn(self.to_dict())
            elapsed = loop.time() - t0
            await asyncio.sleep(max(0.0, tick_dt - elapsed))

        await broadcast_fn(self.to_dict())

    def _tick(self, dt: float) -> None:
        self.tick += 1

        # 1. Regenerate mana.
        for owner in self.mana:
            pp = self._pp[owner]
            self.mana[owner] = min(
                self.mana[owner] + pp["mana_refill_rate"] * dt,
                pp["maximum_mana"],
            )

        # 2. Apply pending moves.
        pending, self._pending = self._pending, []
        for move in pending:
            self._apply_move(move)

        # 3. Snapshot kings that are in PREPARATION for castling (before advancing).
        castling_watch = {
            p.id: p.state
            for p in self.pieces
            if p.type == PieceType.KING and p.pending_castling_rook_id
        }

        # 4. Advance non-moving pieces (PREPARATION → MOVING transitions here).
        for piece in self.pieces:
            if piece.state != PieceState.MOVING:
                piece.advance(dt)

        # 5. Start castling rooks for kings that just entered MOVING this tick.
        for king in self.pieces:
            if (king.type == PieceType.KING
                    and king.id in castling_watch
                    and castling_watch[king.id] == PieceState.PREPARATION
                    and king.state == PieceState.MOVING):
                self._start_castling_rook(king)

        # 6. CCD: advance moving pieces, resolve collisions.
        physics.advance_and_resolve(self.pieces, dt)

        # 7. Promotions: pawns that entered the last rank become queens.
        self._check_promotions()

        # 8. En passant: create ghosts for double-moving pawns that crossed the threshold.
        self._check_ghost_creation()

        # 9. En passant: handle captured or orphaned ghosts.
        self._check_ghost_removals()

        # 10. Win condition.
        self._check_win()

    # ------------------------------------------------------------------
    # Move application
    # ------------------------------------------------------------------

    def _apply_move(self, move: dict) -> None:
        piece = self._find(move["piece_id"])
        if piece is None or piece.state != PieceState.IDLE:
            return
        piece.dest_x = move["dest_x"]
        piece.dest_y = move["dest_y"]
        piece.state = PieceState.PREPARATION
        pp = self._pp[piece.owner]
        piece.state_timer = pp["preparation_period"]
        piece.movement_speed = pp["movement_speed"]
        piece.cooldown_duration = pp["cooldown"]
        piece.freedom_deg = pp["movement_freedom_deg"]

        # Detect castling (king moves > 1 square sideways while unmoved).
        if piece.type == PieceType.KING and not piece.has_moved:
            dx = move["dest_x"] - piece.x
            if abs(dx) > params.SQUARE_SIDE + 1e-6:
                rook_col = 7 if dx > 0 else 0
                rook_x = (rook_col + 0.5) * params.SQUARE_SIDE
                rook = next(
                    (p for p in self.pieces
                     if p.owner == piece.owner
                     and p.type == PieceType.ROOK
                     and abs(p.x - rook_x) < 0.1
                     and abs(p.y - piece.y) < 0.1),
                    None,
                )
                if rook:
                    piece.pending_castling_rook_id = rook.id

        # Detect pawn double move.
        if piece.type == PieceType.PAWN and not piece.has_moved:
            if abs(move["dest_y"] - piece.y) > params.SQUARE_SIDE + 1e-6:
                piece.is_double_move = True
                piece.move_start_y = piece.y

    # ------------------------------------------------------------------
    # Castling
    # ------------------------------------------------------------------

    def _start_castling_rook(self, king: Piece) -> None:
        """Force the castling rook into MOVING state in sync with the king."""
        rook = self._find(king.pending_castling_rook_id)
        king.pending_castling_rook_id = ""

        if rook is None or rook.state != PieceState.IDLE:
            return

        # Rook destination: one square from king's destination toward the rook's side.
        side = 1.0 if king.dest_x > king.x else -1.0
        rook_dest_x = king.dest_x - side * params.SQUARE_SIDE

        king_travel_time = king.state_timer   # already set by _start_moving
        rook_dist = abs(rook.x - rook_dest_x)

        if king_travel_time < 1e-9:
            return

        rook_pp = self._pp[rook.owner]
        rook.dest_x = rook_dest_x
        rook.dest_y = rook.y
        rook.state = PieceState.MOVING
        rook.state_timer = king_travel_time
        rook.vel_x = (rook_dest_x - rook.x) / king_travel_time
        rook.vel_y = 0.0
        rook.has_moved = True
        rook.capture_remaining = 1
        rook.castling_partner_id = king.id
        king.castling_partner_id = rook.id
        rook.movement_speed = rook_pp["movement_speed"]
        rook.cooldown_duration = rook_pp["cooldown"]
        rook.freedom_deg = rook_pp["movement_freedom_deg"]

    # ------------------------------------------------------------------
    # Promotions
    # ------------------------------------------------------------------

    def _check_promotions(self) -> None:
        s = params.SQUARE_SIDE
        for piece in self.pieces:
            if piece.type != PieceType.PAWN:
                continue
            r = piece.diameter / 2.0
            if piece.owner == "white" and piece.y - r < s:
                piece.type = PieceType.QUEEN
            elif piece.owner == "black" and piece.y + r > 7.0 * s:
                piece.type = PieceType.QUEEN

    # ------------------------------------------------------------------
    # En passant
    # ------------------------------------------------------------------

    def _check_ghost_creation(self) -> None:
        s = params.SQUARE_SIDE
        for piece in self.pieces:
            if (piece.type != PieceType.PAWN
                    or not piece.is_double_move
                    or piece.ghost_created):
                continue
            # Ghost is placed where the pawn's center crossed the ±1-square threshold.
            if piece.owner == "white":
                ghost_y = piece.move_start_y - s
                crossed = piece.y <= ghost_y
            else:
                ghost_y = piece.move_start_y + s
                crossed = piece.y >= ghost_y

            if not crossed:
                continue

            ghost_id = f"ghost_{piece.id}_{self.tick}"
            ghost = Piece(
                id=ghost_id,
                type=PieceType.GHOST,
                owner=piece.owner,
                x=piece.x,
                y=ghost_y,
            )
            ghost.diameter = self._pp[ghost.owner]["diameter_piece"]
            self.pieces.append(ghost)
            self._ghost_map[ghost_id] = {"pawn_id": piece.id, "window_closed": False}
            piece.ghost_created = True

    def _check_ghost_removals(self) -> None:
        """
        Remove ghosts whose associated pawn was captured, and vice-versa:
        if a ghost was captured (by an enemy pawn), remove the original pawn.
        """
        piece_ids = {p.id for p in self.pieces}
        for ghost_id, info in list(self._ghost_map.items()):
            pawn_id = info["pawn_id"]
            ghost_alive = ghost_id in piece_ids
            pawn_alive = pawn_id in piece_ids

            if not ghost_alive:
                # Ghost was physically captured → remove original pawn.
                pawn = self._find(pawn_id)
                if pawn:
                    self.pieces.remove(pawn)
                del self._ghost_map[ghost_id]

            elif not pawn_alive:
                # Original pawn was captured → remove orphaned ghost.
                ghost = self._find(ghost_id)
                if ghost:
                    self.pieces.remove(ghost)
                del self._ghost_map[ghost_id]

    def _expire_ghosts(self, moving_piece: Piece, dest_x: float, dest_y: float) -> None:
        """
        Called when a player queues a move. For each active ghost whose pawn has
        finished moving, check if this is the opponent's first move since then.
        If the move does NOT target the ghost, the ghost expires.
        """
        for ghost_id, info in list(self._ghost_map.items()):
            if info["window_closed"]:
                continue

            ghost = self._find(ghost_id)
            if ghost is None:
                del self._ghost_map[ghost_id]
                continue

            # Only the opponent of the ghost's owner triggers expiry.
            if moving_piece.owner == ghost.owner:
                continue

            # Window opens only after the pawn has finished moving.
            pawn = self._find(info["pawn_id"])
            if pawn and pawn.state in (PieceState.MOVING, PieceState.PREPARATION):
                continue

            # Is this move aiming at the ghost?
            if self._targets_ghost(moving_piece, dest_x, dest_y, ghost):
                info["window_closed"] = True   # window used; ghost stays alive
            else:
                self.pieces.remove(ghost)
                del self._ghost_map[ghost_id]

    def _targets_ghost(self, piece: Piece, dest_x: float, dest_y: float,
                       ghost: Piece) -> bool:
        if piece.type != PieceType.PAWN:
            return False
        return math.hypot(ghost.x - dest_x, ghost.y - dest_y) < piece.diameter

    # ------------------------------------------------------------------
    # Win condition
    # ------------------------------------------------------------------

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
        return {
            "type": GAME_STATE,
            "tick": self.tick,
            "pieces": [p.to_dict() for p in self.pieces],
            "mana": {k: round(v, 3) for k, v in self.mana.items()},
            "max_mana":    {c: self._pp[c]["maximum_mana"] for c in ("white", "black")},
            "freedom_deg": {c: self._pp[c]["movement_freedom_deg"] for c in ("white", "black")},
            "prep_period": {c: self._pp[c]["preparation_period"] for c in ("white", "black")},
            "cooldown":    {c: self._pp[c]["cooldown"] for c in ("white", "black")},
            "player_params": {
                c: {
                    "base_move_cost": self._pp[c]["base_move_cost"],
                    "distance_cost":  self._pp[c]["distance_cost"],
                }
                for c in ("white", "black")
            },
            "countdown": self.countdown,
            "game_over": self.game_over,
            "winner": self.winner,
        }

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
