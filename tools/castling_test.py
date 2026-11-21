"""
Castling partners may overlap only until they separate.

    PYTHONPATH=. python3 tools/castling_test.py

A castling king and rook cross each other, so physics lets the pair overlap
for the transit. That permission has to end: once the two have come apart
they are ordinary pieces again and must block each other like any others.
"""

from server.pieces import PieceType, PieceState
from tools.pawn_test import make_game, other, run, check


def overlapping_pair():
    """King and rook left on top of each other by a castling, both idle."""
    king = other("K", PieceType.KING, "white", 5.5, 7.5)
    rook = other("R", PieceType.ROOK, "white", 5.7, 7.5)
    king.has_moved = rook.has_moved = True
    king.castling_partner_id, rook.castling_partner_id = "R", "K"
    return make_game(king, rook), king, rook


def test_transit_passes_through() -> None:
    """The castling itself: king and rook swap sides, crossing on the way."""
    king = other("K", PieceType.KING, "white", 4.5, 7.5)
    rook = other("R", PieceType.ROOK, "white", 7.5, 7.5)
    g = make_game(king, rook)
    assert g.queue_move("K", (6.5, 7.5), "white") is None, "castling rejected"
    run(g, 4.0)
    check("castling king reaches its square", abs(king.x - 6.5) < 1e-6, f"king x {king.x}")
    check("castling rook crosses the king", abs(rook.x - 5.5) < 1e-6, f"rook x {rook.x}")
    check("partners released after a clean castling",
          king.castling_partner_id == "" and rook.castling_partner_id == "")


def test_overlap_can_separate() -> None:
    """While still overlapping, either piece may move out of the other."""
    g, king, rook = overlapping_pair()
    assert g.queue_move("R", (5.7, 5.5), "white") is None
    run(g, 4.0)
    check("rook moves out of the overlap", abs(rook.y - 5.5) < 1e-6, f"rook y {rook.y}")
    check("partners released once apart",
          king.castling_partner_id == "" and rook.castling_partner_id == "")


def test_separated_pair_blocks() -> None:
    """After separating, the rook may not pass back through the king."""
    g, king, rook = overlapping_pair()
    assert g.queue_move("R", (5.7, 5.5), "white") is None
    run(g, 4.0)
    assert g.queue_move("R", (5.7, 7.9), "white") is None
    run(g, 4.0)
    gap = ((rook.x - king.x) ** 2 + (rook.y - king.y) ** 2) ** 0.5
    check("rook is stopped by the king it castled with",
          rook.y < 7.0 and gap >= king.radius + rook.radius - 1e-6,
          f"rook at ({rook.x:.3f}, {rook.y:.3f}), gap {gap:.3f}")
    check("both still on the board", {p.id for p in g.pieces} == {"K", "R"})
    check("rook ended idle or cooling down",
          rook.state in (PieceState.COOLDOWN, PieceState.IDLE))


if __name__ == "__main__":
    test_transit_passes_through()
    test_overlap_can_separate()
    test_separated_pair_blocks()
    print("all castling tests passed")
