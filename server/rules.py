"""
Move validation: direction sectors, range caps, piece-specific rules.
Called by GameState.queue_move before mana is deducted.
"""

import math

from . import params
from .pieces import Piece, PieceType, PieceState

_SQRT2 = math.sqrt(2.0)
_FREEDOM_RAD = math.radians(params.MOVEMENT_FREEDOM_DEG)

_ORTHO: list[tuple[float, float]] = [
    (1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0),
]
_DIAG: list[tuple[float, float]] = [
    ( 1.0/_SQRT2,  1.0/_SQRT2),
    ( 1.0/_SQRT2, -1.0/_SQRT2),
    (-1.0/_SQRT2,  1.0/_SQRT2),
    (-1.0/_SQRT2, -1.0/_SQRT2),
]
_ALL8 = _ORTHO + _DIAG


def validate_move(piece: Piece, dest_x: float, dest_y: float,
                  all_pieces: list[Piece]) -> str | None:
    """Return a rejection reason string, or None if the move is legal."""
    dx = dest_x - piece.x
    dy = dest_y - piece.y
    if math.hypot(dx, dy) < 1e-6:
        return "zero-distance move"

    match piece.type:
        case PieceType.ROOK:
            if not _in_sector(dx, dy, _ORTHO):
                return "rook must move orthogonally"
        case PieceType.BISHOP:
            if not _in_sector(dx, dy, _DIAG):
                return "bishop must move diagonally"
        case PieceType.QUEEN:
            if not _in_sector(dx, dy, _ALL8):
                return "queen must move orthogonally or diagonally"
        case PieceType.KING:
            return _check_king(piece, dx, dy, all_pieces)
        case PieceType.PAWN:
            return _check_pawn(piece, dest_x, dest_y, dx, dy, all_pieces)
        case PieceType.KNIGHT:
            return _check_knight(dx, dy)
    return None


# ---------------------------------------------------------------------------
# Direction helpers
# ---------------------------------------------------------------------------

def _in_sector(dx: float, dy: float, dirs: list[tuple[float, float]]) -> bool:
    """True if (dx, dy) is within MOVEMENT_FREEDOM_DEG of any direction in dirs."""
    length = math.hypot(dx, dy)
    for lx, ly in dirs:
        dot = (dx * lx + dy * ly) / length
        if math.acos(max(-1.0, min(1.0, dot))) <= _FREEDOM_RAD:
            return True
    return False


def _closest_cap(dx: float, dy: float) -> float:
    """Max move distance for Type-2 pieces, based on the nearest legal direction."""
    length = math.hypot(dx, dy)
    best_dot, best_is_ortho = -2.0, True
    for lx, ly in _ALL8:
        dot = (dx * lx + dy * ly) / length
        if dot > best_dot:
            best_dot = dot
            best_is_ortho = (lx == 0.0 or ly == 0.0)
    return params.SQUARE_SIDE if best_is_ortho else params.SQUARE_SIDE * _SQRT2


# ---------------------------------------------------------------------------
# Per-piece checks
# ---------------------------------------------------------------------------

def _check_king(piece: Piece, dx: float, dy: float,
                all_pieces: list[Piece]) -> str | None:
    dist = math.hypot(dx, dy)

    # Castling: unmoved king, strictly horizontal, distance (1, 2] squares
    if (not piece.has_moved
            and _in_sector(dx, dy, [(1.0, 0.0), (-1.0, 0.0)])
            and params.SQUARE_SIDE < dist <= 2 * params.SQUARE_SIDE):
        return _check_castling(piece, dx, all_pieces)

    # Normal king move
    if not _in_sector(dx, dy, _ALL8):
        return "king must move orthogonally or diagonally"
    if dist > _closest_cap(dx, dy):
        return "king can only move 1 square"
    return None


def _check_castling(piece: Piece, dx: float,
                    all_pieces: list[Piece]) -> str | None:
    rook_col = 7 if dx > 0 else 0
    rook_x = (rook_col + 0.5) * params.SQUARE_SIDE
    rook = next(
        (p for p in all_pieces
         if p.owner == piece.owner
         and p.type == PieceType.ROOK
         and abs(p.x - rook_x) < 0.1
         and abs(p.y - piece.y) < 0.1),
        None,
    )
    if rook is None:
        return "no rook available for castling"
    if rook.has_moved:
        return "rook has already moved"
    if rook.state != PieceState.IDLE:
        return "rook is not idle"
    return None


def _check_pawn(piece: Piece, dest_x: float, dest_y: float,
                dx: float, dy: float, all_pieces: list[Piece]) -> str | None:
    forward_dy = -1.0 if piece.owner == "white" else 1.0
    dist = math.hypot(dx, dy)

    # Forward move (no captures allowed)
    if _in_sector(dx, dy, [(0.0, forward_dy)]):
        max_fwd = (2 if not piece.has_moved else 1) * params.SQUARE_SIDE
        if dist > max_fwd:
            return "pawn cannot move that far forward"
        return None

    # Diagonal capture
    diag_dirs = [
        ( 1.0 / _SQRT2, forward_dy / _SQRT2),
        (-1.0 / _SQRT2, forward_dy / _SQRT2),
    ]
    if _in_sector(dx, dy, diag_dirs):
        if dist > params.SQUARE_SIDE * _SQRT2:
            return "pawn diagonal move too far"
        if not _enemy_in_path(piece, dest_x, dest_y, all_pieces):
            return "pawn can only move diagonally to capture"
        return None

    return "pawn can only move forward or diagonally forward to capture"


def _enemy_in_path(piece: Piece, dest_x: float, dest_y: float,
                   all_pieces: list[Piece]) -> bool:
    """True if any enemy piece's centre lies within DIAMETER_PIECE of the move path."""
    R = params.DIAMETER_PIECE
    for other in all_pieces:
        if other.owner == piece.owner:
            continue
        if seg_dist(other.x, other.y, piece.x, piece.y, dest_x, dest_y) <= R:
            return True
    return False


def seg_dist(px: float, py: float,
              ax: float, ay: float,
              bx: float, by: float) -> float:
    """Shortest distance from point (px, py) to segment (ax, ay)–(bx, by)."""
    ddx, ddy = bx - ax, by - ay
    seg_sq = ddx * ddx + ddy * ddy
    if seg_sq < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * ddx + (py - ay) * ddy) / seg_sq))
    return math.hypot(px - (ax + t * ddx), py - (ay + t * ddy))


def _check_knight(dx: float, dy: float) -> str | None:
    s = params.SQUARE_SIDE
    r = math.sqrt(5.0) * s * math.tan(_FREEDOM_RAD)
    targets = (
        [(a * s, b * s) for a in (2.0, -2.0) for b in (1.0, -1.0)] +
        [(a * s, b * s) for a in (1.0, -1.0) for b in (2.0, -2.0)]
    )
    for tx, ty in targets:
        if math.hypot(dx - tx, dy - ty) <= r:
            return None
    return "knight destination not within any landing circle"
