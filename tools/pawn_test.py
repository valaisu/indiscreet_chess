"""
The reworked pawn: one capture per diagonal landing, promotion on the
centerpoint, and no interaction between the two.

    PYTHONPATH=. python3 tools/pawn_test.py

The dangerous case is a diagonal capture that lands on the last rank. The pawn
promotes in mid-flight, and every rule about the move it is executing —
immunity, the arrival burst, the spent capture — has to survive it turning into
a queen. Deriving any of that from the piece's type is how it breaks.
"""

import math

from server import params
from server.game import GameState
from server.pieces import Piece, PieceType, PieceState


def make_game(*pieces: Piece) -> GameState:
    """A started game holding exactly these pieces."""
    g = GameState()
    g.pieces = list(pieces)
    for p in g.pieces:
        p.diameter = params.DIAMETER_PIECE
    g.started = True
    g.mana = {"white": 99.0, "black": 99.0}
    return g


def pawn(pid: str, owner: str, x: float, y: float) -> Piece:
    return Piece(id=pid, type=PieceType.PAWN, owner=owner, x=x, y=y)


def other(pid: str, kind: PieceType, owner: str, x: float, y: float) -> Piece:
    return Piece(id=pid, type=kind, owner=owner, x=x, y=y)


def run(g: GameState, seconds: float) -> None:
    dt = 1.0 / params.TICK_RATE
    for _ in range(int(seconds / dt)):
        g._tick(dt)


def ids(g: GameState) -> set[str]:
    return {p.id for p in g.pieces}


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail or 'failed'}")
    print(f"[PASS] {name}")


# --- one capture per diagonal landing ---------------------------------------

def test_one_capture_only() -> None:
    """Two enemies inside the landing circle: the nearer one dies, alone."""
    p = pawn("p", "white", 4.5, 4.5)
    near = other("near", PieceType.ROOK, "black", 3.55, 3.55)
    far = other("far", PieceType.ROOK, "black", 3.35, 3.35)
    g = make_game(p, near, far)

    assert g.queue_move("p", (3.5, 3.5), "white") is None, "diagonal capture rejected"
    run(g, 2.0)

    check("diagonal capture takes exactly one piece",
          ids(g) == {"p", "far"}, f"survivors {sorted(ids(g))}")
    check("the nearer piece is the one taken", "near" not in ids(g))


def test_friend_blocks_the_landing() -> None:
    """Own pieces are never captured. One in the way stops the pawn short, and
    the enemy behind it survives."""
    p = pawn("p", "white", 4.5, 4.5)
    friend = other("friend", PieceType.BISHOP, "white", 3.5, 3.5)
    enemy = other("enemy", PieceType.ROOK, "black", 3.3, 3.3)
    g = make_game(p, friend, enemy)

    assert g.queue_move("p", (3.5, 3.5), "white") is None
    run(g, 2.0)
    check("nothing is captured through a friendly piece",
          ids(g) == {"p", "friend", "enemy"}, f"survivors {sorted(ids(g))}")
    check("the pawn stopped short of its destination",
          math.hypot(p.x - 3.5, p.y - 3.5) > 1e-6, f"({p.x}, {p.y})")
    check("it stopped touching the friend, not overlapping it",
          abs(math.hypot(p.x - friend.x, p.y - friend.y)
              - (p.radius + friend.radius)) < 1e-6,
          f"gap {math.hypot(p.x - friend.x, p.y - friend.y) - (p.radius + friend.radius)}")
    check("and the move is over", p.state != PieceState.MOVING, str(p.state))
    check("its capture was not spent", p.capture_remaining == 1)


def test_enemy_does_not_block() -> None:
    """Enemies are still passed through: only the landing resolves."""
    p = pawn("p", "white", 4.5, 4.5)
    shield = other("shield", PieceType.ROOK, "black", 4.0, 4.0)
    target = other("target", PieceType.ROOK, "black", 3.5, 3.5)
    g = make_game(p, shield, target)

    assert g.queue_move("p", (3.5, 3.5), "white") is None
    run(g, 2.0)
    check("passed through the enemy on the way",
          ids(g) == {"p", "shield"}, f"survivors {sorted(ids(g))}")
    check("reached the landing point",
          math.hypot(p.x - 3.5, p.y - 3.5) < 1e-6, f"({p.x}, {p.y})")


# --- promotion on the centerpoint -------------------------------------------

def test_promotes_on_centerpoint() -> None:
    """A pawn whose hitbox overlaps the last rank is still a pawn; it promotes
    only once its centre is inside it."""
    p = pawn("p", "white", 4.5, 2.2)
    g = make_game(p)

    # Stops at y=1.25: hitbox inside the last rank, centre not. The old rule
    # promoted here, and a larger pawn promoted earlier still.
    assert g.queue_move("p", (4.5, 1.25), "white") is None
    run(g, 2.0)
    check("hitbox already overlaps the last rank", p.y - p.radius < 1.0,
          f"y={p.y} r={p.radius}")
    check("still a pawn while its centre is short of it",
          p.type == PieceType.PAWN, str(p.type))

    assert g.queue_move("p", (4.5, 0.9), "white") is None
    run(g, 2.0)
    check("promotes once the centre crosses", p.type == PieceType.QUEEN, str(p.type))


# --- promotion during a diagonal capture ------------------------------------

def test_promotion_mid_capture() -> None:
    """Landing on the last rank promotes and captures — once."""
    p = pawn("p", "white", 4.5, 1.5)
    victim = other("victim", PieceType.ROOK, "black", 3.5, 0.5)
    behind = other("behind", PieceType.ROOK, "black", 3.3, 0.5)
    g = make_game(p, victim, behind)

    assert g.queue_move("p", (3.5, 0.5), "white") is None
    # Mid-flight the centre crosses y=1 while the move is still running.
    dt = 1.0 / params.TICK_RATE
    saw_moving_queen = False
    for _ in range(int(2.0 / dt)):
        g._tick(dt)
        if p.type == PieceType.QUEEN and p.state == PieceState.MOVING:
            saw_moving_queen = True

    check("promoted in mid-flight", saw_moving_queen)
    check("the burst still fired after promoting",
          "victim" not in ids(g), f"survivors {sorted(ids(g))}")
    check("promotion did not buy a second capture",
          "behind" in ids(g), f"survivors {sorted(ids(g))}")
    check("the pawn survived and is a queen", p.type == PieceType.QUEEN)
    check("its capture budget is spent, not refilled", p.capture_remaining == 0,
          str(p.capture_remaining))


def test_immunity_survives_promotion() -> None:
    """A diagonal capture passes through everything, promotion or not."""
    p = pawn("p", "white", 4.5, 1.5)
    blocker = other("blocker", PieceType.ROOK, "black", 4.0, 1.0)
    target = other("target", PieceType.ROOK, "black", 3.5, 0.5)
    g = make_game(p, blocker, target)

    assert g.queue_move("p", (3.5, 0.5), "white") is None
    run(g, 2.0)
    check("passed through the blocker", "blocker" in ids(g))
    check("landed and took its one target", "target" not in ids(g))
    check("arrived at the destination",
          math.hypot(p.x - 3.5, p.y - 0.5) < 1e-6, f"({p.x}, {p.y})")


# --- en passant -------------------------------------------------------------

def test_en_passant_still_resolves() -> None:
    """The ghost belongs to the opponent, so restricting the burst to enemies
    must not have made it uncapturable."""
    black = pawn("black_pawn", "black", 3.5, 1.5)
    white = pawn("white_pawn", "white", 4.5, 3.5)
    g = make_game(black, white)

    assert g.queue_move("black_pawn", (3.5, 3.5), "black") is None, "double move rejected"
    run(g, 2.0)
    ghost = next((p for p in g.pieces if p.type == PieceType.GHOST), None)
    check("the double move left a ghost", ghost is not None)
    check("the ghost sits where the pawn crossed",
          abs(ghost.y - 2.5) < 1e-6, f"y={ghost.y}")

    assert g.queue_move("white_pawn", (ghost.x, ghost.y), "white") is None, \
        "en passant rejected"
    run(g, 2.0)
    check("the ghost was taken", ghost.id not in ids(g))
    check("and the pawn that left it went with it",
          "black_pawn" not in ids(g), f"survivors {sorted(ids(g))}")
    check("the capturing pawn survives", "white_pawn" in ids(g))


# --- the opening position ---------------------------------------------------

def test_start_overlap_rejected() -> None:
    from server.pieces import start_overlap_reason
    check("default sizes are fine", start_overlap_reason(None) is None)
    check("just under one square is fine",
          start_overlap_reason({"diameter_piece": 0.99}) is None)
    check("exactly one square is rejected",
          start_overlap_reason({"diameter_piece": 1.0}) is not None)
    check("an oversized pawn is rejected against the rook behind it",
          start_overlap_reason(
              {"diameter_piece": 0.6, "pieces": {"pawn": {"diameter_piece": 1.45}}}
          ) is not None)


if __name__ == "__main__":
    test_one_capture_only()
    test_friend_blocks_the_landing()
    test_enemy_does_not_block()
    test_promotes_on_centerpoint()
    test_promotion_mid_capture()
    test_immunity_survives_promotion()
    test_en_passant_still_resolves()
    test_start_overlap_rejected()
    print("PASS: pawn capture, promotion and opening geometry")
