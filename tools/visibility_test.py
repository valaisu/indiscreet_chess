"""
Assert that hidden information never leaves the server.

    PYTHONPATH=. python3 tools/visibility_test.py

The renderer choosing not to draw something is not privacy: a client can read
its own socket. This checks the snapshot itself — with every view option off,
the opponent's preparing piece must look idle, its destination must be its own
square, and their mana must be absent entirely.
"""
from server.game import GameState
from server.pieces import PieceState

g = GameState(view={"enemy_mana": False, "enemy_prep": False,
                    "enemy_cooldown": False, "enemy_dest": False})
g.started = True
p = next(x for x in g.pieces if x.id == "b_pawn_3")
g.queue_move("b_pawn_3", (p.x, p.y + 1.0), "black")
g._tick(0.05)

def look(snap, pid):
    return next(d for d in snap["pieces"] if d["id"] == pid)

white = g.to_dict("white")
black = g.to_dict("black")
solo  = g.to_dict(None)
print("piece state:", p.state)
print("white sees black pawn:", look(white, "b_pawn_3")["state"],
      "dest", (look(white, "b_pawn_3")["dest_x"], look(white, "b_pawn_3")["dest_y"]))
print("black sees own pawn:  ", look(black, "b_pawn_3")["state"],
      "dest", (look(black, "b_pawn_3")["dest_x"], look(black, "b_pawn_3")["dest_y"]))
print("white mana keys:", sorted(white["mana"]), " black:", sorted(black["mana"]),
      " solo:", sorted(solo["mana"]))
assert p.state == PieceState.PREPARATION
assert look(white, "b_pawn_3")["state"] == "idle"
assert look(black, "b_pawn_3")["state"] == "preparation"
assert sorted(white["mana"]) == ["white"] and sorted(black["mana"]) == ["black"]
assert sorted(solo["mana"]) == ["black", "white"]
print("diameter on the wire:", look(white, "w_pawn_0")["d"])
print("PASS: hidden information is absent from the snapshot")
