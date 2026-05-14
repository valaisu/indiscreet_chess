"""
Continuous collision detection and resolution.

Called once per tick from GameState._tick():
    physics.advance_and_resolve(self.pieces, dt)

Algorithm
---------
Knights are immune during movement and advance independently; burst capture
fires on arrival.

For all other movers we run an event-driven CCD loop:

  while remaining_dt > 0:
    find earliest of:
      - collision: smallest t from parametric circle sweeps across all
                   (mover, non-immune-piece) pairs
      - arrival:   smallest state_timer among movers
    advance all movers to that t
    resolve the event (collision or natural stop)
    remaining_dt -= t

This guarantees correct ordering when multiple pieces are moving simultaneously.
"""

import math

from . import params
from .pieces import Piece, PieceType, PieceState

# Small epsilon to avoid re-detecting the same surface contact.
_EPS = 1e-9


def advance_and_resolve(pieces: list[Piece], dt: float) -> None:
    """Main physics entry point. Mutates the pieces list in-place."""
    _advance_knights(pieces, dt)
    _advance_diagonal_pawns(pieces, dt)
    _ccd_loop(pieces, dt)


# ---------------------------------------------------------------------------
# Knights
# ---------------------------------------------------------------------------

def _advance_knights(pieces: list[Piece], dt: float) -> None:
    to_remove: set[int] = set()

    for knight in list(pieces):
        if knight.type != PieceType.KNIGHT or knight.state != PieceState.MOVING:
            continue
        knight.advance(dt)
        if knight.state == PieceState.COOLDOWN and id(knight) not in to_remove:
            _knight_burst(knight, pieces, to_remove)

    pieces[:] = [p for p in pieces if id(p) not in to_remove]


def _diagonal_pawn_burst(pawn: Piece, pieces: list[Piece], to_remove: set) -> None:
    """On arrival capture all overlapping pieces; remove pawn if any of those were moving."""
    for other in pieces:
        if other is pawn or id(other) in to_remove:
            continue
        # Pieces immune during their own travel are skipped
        if other.state == PieceState.MOVING:
            if other.type == PieceType.KNIGHT or _is_diagonal_pawn(other):
                continue
        dist = math.hypot(other.x - pawn.x, other.y - pawn.y)
        if dist <= pawn.radius + other.radius:
            to_remove.add(id(other))
            if other.state == PieceState.MOVING:
                to_remove.add(id(pawn))


def _advance_diagonal_pawns(pieces: list[Piece], dt: float) -> None:
    to_remove: set[int] = set()

    for pawn in list(pieces):
        if not _is_diagonal_pawn(pawn):
            continue
        pawn.advance(dt)
        if pawn.state == PieceState.COOLDOWN and id(pawn) not in to_remove:
            _diagonal_pawn_burst(pawn, pieces, to_remove)

    pieces[:] = [p for p in pieces if id(p) not in to_remove]


def _knight_burst(knight: Piece, pieces: list[Piece], to_remove: set) -> None:
    """Remove all pieces overlapping the knight on arrival; remove knight if any were moving."""
    for other in pieces:
        if other is knight or id(other) in to_remove:
            continue
        if other.type == PieceType.GHOST:
            continue
        if _is_diagonal_pawn(other):  # immune during diagonal travel
            continue
        dist = math.hypot(other.x - knight.x, other.y - knight.y)
        if dist <= knight.radius + other.radius:
            to_remove.add(id(other))
            if other.state == PieceState.MOVING:
                to_remove.add(id(knight))


# ---------------------------------------------------------------------------
# CCD loop
# ---------------------------------------------------------------------------

def _ccd_loop(pieces: list[Piece], dt: float) -> None:
    removed: set[int] = set()
    remaining = dt

    while remaining > _EPS:
        movers = [p for p in pieces
                  if p.state == PieceState.MOVING
                  and id(p) not in removed
                  and p.type != PieceType.KNIGHT
                  and not _is_diagonal_pawn(p)]

        if not movers:
            break

        # --- Find earliest collision ---
        col_t = remaining + 1.0   # sentinel: > remaining means none found
        col_pair: tuple[Piece, Piece] | None = None

        for a in movers:
            for b in pieces:
                if b is a or id(b) in removed:
                    continue
                # Knights in MOVING state are immune to collision
                if b.type == PieceType.KNIGHT and b.state == PieceState.MOVING:
                    continue
                # Diagonal-capturing pawns are immune during travel
                if _is_diagonal_pawn(b):
                    continue
                # Castling partners are allowed to overlap during transit
                if a.castling_partner_id and b.id == a.castling_partner_id:
                    continue
                t = _sweep_time(a, b, remaining)
                if t is not None and t < col_t:
                    col_t = t
                    col_pair = (a, b)

        # --- Find earliest arrival ---
        arr_t = remaining + 1.0   # sentinel
        for p in movers:
            if p.state_timer < arr_t:
                arr_t = p.state_timer

        # --- Pick next event ---
        if col_t <= remaining and col_t <= arr_t:
            event_t = col_t
            is_collision = True
        elif arr_t <= remaining:
            event_t = arr_t
            is_collision = False
        else:
            # No events left: advance all movers to end of remaining.
            for p in movers:
                p._advance_movement(remaining)
            break

        # Advance all movers to the event time.
        for p in movers:
            p._advance_movement(event_t)
        remaining -= event_t

        if is_collision:
            assert col_pair is not None
            a, b = col_pair
            _resolve_collision(a, b, removed)
        # Arrival events are handled implicitly: _advance_movement transitions
        # the piece to COOLDOWN when state_timer is exhausted.

    pieces[:] = [p for p in pieces if id(p) not in removed]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_forward_pawn(piece: Piece) -> bool:
    """True when this pawn is moving in its forward (non-capturing) direction."""
    if piece.type != PieceType.PAWN:
        return False
    speed = math.hypot(piece.vel_x, piece.vel_y)
    if speed < 1e-9:
        return False
    forward_dy = -1.0 if piece.owner == "white" else 1.0
    dot = (piece.vel_y * forward_dy) / speed
    freedom = math.radians(params.MOVEMENT_FREEDOM_DEG)
    return math.acos(max(-1.0, min(1.0, dot))) <= freedom


def _is_diagonal_pawn(piece: Piece) -> bool:
    """True when this pawn is executing a diagonal capture move (immune during travel)."""
    return (piece.type == PieceType.PAWN
            and piece.state == PieceState.MOVING
            and not _is_forward_pawn(piece))


def _continue_after_capture(piece: Piece, cx: float, cy: float) -> None:
    """Continue piece in same direction, stopping when cx,cy is perpendicular."""
    speed = params.MOVEMENT_SPEED
    ux = piece.vel_x / speed
    uy = piece.vel_y / speed
    perp_dist = max(0.0, (cx - piece.x) * ux + (cy - piece.y) * uy)
    remaining_dist = piece.state_timer * speed
    t = min(perp_dist, remaining_dist)
    piece.dest_x = piece.x + t * ux
    piece.dest_y = piece.y + t * uy
    piece.state_timer = t / speed


# ---------------------------------------------------------------------------
# Parametric sweep
# ---------------------------------------------------------------------------

def _sweep_time(a: Piece, b: Piece, max_t: float) -> float | None:
    """
    Return the earliest t in (_EPS, max_t] at which moving circle a first
    touches circle b (b may be stationary or moving at constant velocity).
    Returns None if no contact occurs within max_t.
    """
    px = a.x - b.x
    py = a.y - b.y
    vx = a.vel_x - (b.vel_x if b.state == PieceState.MOVING else 0.0)
    vy = a.vel_y - (b.vel_y if b.state == PieceState.MOVING else 0.0)
    R = a.radius + b.radius

    # Ghosts can only be captured by enemy pawns moving diagonally.
    if b.type == PieceType.GHOST:
        if a.type != PieceType.PAWN or a.owner == b.owner or _is_forward_pawn(a):
            return None

    # If already touching or overlapping, only collide if approaching.
    # Separating pieces are let go freely (e.g. after a capture continuation or castling).
    if px * px + py * py <= R * R:
        if vx * px + vy * py >= 0.0:
            return None   # separating or parallel — let them move apart
        return _EPS       # approaching while in contact → immediate collision

    # Quadratic: (v·v)t² + 2(p·v)t + (p·p − R²) = 0
    A = vx * vx + vy * vy
    if A < 1e-12:
        return None  # no relative motion

    B = 2.0 * (px * vx + py * vy)
    C = px * px + py * py - R * R  # positive: pieces are separated

    disc = B * B - 4.0 * A * C
    if disc < 0.0:
        return None

    t1 = (-B - math.sqrt(disc)) / (2.0 * A)

    # t1 ≤ _EPS means pieces are essentially at contact (dist barely above R due
    # to float drift from a previous stop).  Apply the same approach-direction
    # test used for the overlapping case instead of falling through to t2.
    if t1 <= _EPS:
        return None if B >= 0.0 else _EPS

    return t1 if t1 <= max_t else None


# ---------------------------------------------------------------------------
# Collision resolution
# ---------------------------------------------------------------------------

def _resolve_collision(a: Piece, b: Piece, removed: set) -> None:
    """
    Called when moving piece a has just touched piece b.
    Determines capture vs block and updates piece states in-place.
    """
    b_moving = b.state == PieceState.MOVING
    b_immune = b.type == PieceType.KNIGHT and b_moving

    # Forward-moving pawns cannot capture anything.
    a_captures_b = (
        b.owner != a.owner
        and a.capture_remaining > 0
        and not b_immune
        and not _is_forward_pawn(a)
    )
    b_captures_a = (
        b_moving
        and b.owner != a.owner
        and b.capture_remaining > 0
    )

    if a_captures_b:
        # Ghost capture: remove ghost, pawn continues to its original destination.
        if b.type == PieceType.GHOST:
            removed.add(id(b))
            return

        removed.add(id(b))
        a.capture_remaining -= 1

        if b_captures_a:
            removed.add(id(a))
        else:
            _continue_after_capture(a, b.x, b.y)

    elif b_captures_a:
        # A can't (or won't) capture B, but B is a moving enemy that can capture A.
        removed.add(id(a))
        b.capture_remaining -= 1
        _continue_after_capture(b, a.x, a.y)

    else:
        # A is blocked: push it to the exact contact surface so the stored
        # distance is precisely R rather than R ± float drift.
        dx = a.x - b.x
        dy = a.y - b.y
        dist = math.hypot(dx, dy)
        R = a.radius + b.radius
        if dist > 1e-9:
            a.stop_at(b.x + dx / dist * R, b.y + dy / dist * R)
        else:
            a.stop_at(a.x, a.y)
