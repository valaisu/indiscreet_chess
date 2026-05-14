import math
from dataclasses import dataclass, field
from enum import Enum

from . import params


class PieceType(Enum):
    PAWN = "pawn"
    ROOK = "rook"
    KNIGHT = "knight"
    BISHOP = "bishop"
    QUEEN = "queen"
    KING = "king"
    GHOST = "ghost"   # en passant ghost — stationary, capturable by enemy pawns only


class PieceState(Enum):
    IDLE = "idle"
    PREPARATION = "preparation"
    MOVING = "moving"
    COOLDOWN = "cooldown"


@dataclass
class Piece:
    id: str
    type: PieceType
    owner: str          # "white" | "black"
    x: float
    y: float
    state: PieceState = PieceState.IDLE
    state_timer: float = 0.0   # seconds remaining in current state phase
    dest_x: float = 0.0
    dest_y: float = 0.0
    vel_x: float = 0.0
    vel_y: float = 0.0
    has_moved: bool = False          # used by castling and pawn double-move logic
    capture_remaining: int = 1       # captures left this move; reset on each move start
    # Castling
    castling_partner_id: str = ""    # ID of king/rook partner during castling transit
    pending_castling_rook_id: str = ""  # set on king during PREPARATION for a castling move
    # En passant ghost creation
    is_double_move: bool = False     # this move is a pawn double-step
    ghost_created: bool = False      # ghost has already been spawned for this move
    move_start_y: float = 0.0       # pawn's y at the moment the double move was queued
    # Per-player params (set from owner's handicap params when a move is queued)
    movement_speed: float = field(default_factory=lambda: params.MOVEMENT_SPEED)
    cooldown_duration: float = field(default_factory=lambda: params.COOLDOWN)
    diameter: float = field(default_factory=lambda: params.DIAMETER_PIECE)
    freedom_deg: float = field(default_factory=lambda: params.MOVEMENT_FREEDOM_DEG)

    @property
    def radius(self) -> float:
        return self.diameter / 2.0

    def advance(self, dt: float) -> None:
        """Advance state machine by dt seconds. No collision detection."""
        if self.state == PieceState.PREPARATION:
            self.state_timer -= dt
            if self.state_timer <= 0.0:
                self._start_moving()
        elif self.state == PieceState.MOVING:
            self._advance_movement(dt)
        elif self.state == PieceState.COOLDOWN:
            self.state_timer -= dt
            if self.state_timer <= 0.0:
                self.state = PieceState.IDLE
                self.state_timer = 0.0

    def _start_moving(self) -> None:
        dx = self.dest_x - self.x
        dy = self.dest_y - self.y
        dist = math.hypot(dx, dy)
        if dist < 1e-9:
            self._arrive()
            return
        speed = self.movement_speed
        self.vel_x = dx / dist * speed
        self.vel_y = dy / dist * speed
        self.state = PieceState.MOVING
        self.state_timer = dist / speed   # time until arrival
        self.has_moved = True
        self.capture_remaining = 1

    def _advance_movement(self, dt: float) -> None:
        if dt >= self.state_timer:
            self.x = self.dest_x
            self.y = self.dest_y
            self._arrive()
        else:
            self.x += self.vel_x * dt
            self.y += self.vel_y * dt
            self.state_timer -= dt

    def _arrive(self) -> None:
        self.vel_x = 0.0
        self.vel_y = 0.0
        self.state = PieceState.COOLDOWN
        self.state_timer = self.cooldown_duration

    def stop_at(self, x: float, y: float) -> None:
        """Halt the piece at a given position (used by collision resolution)."""
        self.x = x
        self.y = y
        self._arrive()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "owner": self.owner,
            "x": round(self.x, 4),
            "y": round(self.y, 4),
            "state": self.state.value,
            "state_timer": round(self.state_timer, 4),
            "dest_x": round(self.dest_x, 4),
            "dest_y": round(self.dest_y, 4),
            "vel_x": round(self.vel_x, 4),
            "vel_y": round(self.vel_y, 4),
            "has_moved": self.has_moved,
        }


# Column order for the back rank, left to right from both sides' perspective.
_BACK_RANK = [
    PieceType.ROOK,
    PieceType.KNIGHT,
    PieceType.BISHOP,
    PieceType.QUEEN,
    PieceType.KING,
    PieceType.BISHOP,
    PieceType.KNIGHT,
    PieceType.ROOK,
]


def initial_board() -> list[Piece]:
    """Return pieces in their standard chess starting positions.

    Board coordinates: x in [0, 8], y in [0, 8].
    y=0 is black's back rank; y=8 is white's back rank.
    Each square's centre is at (col + 0.5, row + 0.5).
    White moves toward lower y; black moves toward higher y.
    """
    pieces: list[Piece] = []
    s = params.SQUARE_SIDE

    for col, piece_type in enumerate(_BACK_RANK):
        cx = (col + 0.5) * s
        pieces.append(Piece(
            id=f"b_{piece_type.value}_{col}",
            type=piece_type,
            owner="black",
            x=cx,
            y=0.5 * s,
        ))
        pieces.append(Piece(
            id=f"w_{piece_type.value}_{col}",
            type=piece_type,
            owner="white",
            x=cx,
            y=7.5 * s,
        ))

    for col in range(8):
        cx = (col + 0.5) * s
        pieces.append(Piece(
            id=f"b_pawn_{col}",
            type=PieceType.PAWN,
            owner="black",
            x=cx,
            y=1.5 * s,
        ))
        pieces.append(Piece(
            id=f"w_pawn_{col}",
            type=PieceType.PAWN,
            owner="white",
            x=cx,
            y=6.5 * s,
        ))

    return pieces
