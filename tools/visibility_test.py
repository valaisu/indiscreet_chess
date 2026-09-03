"""
Assert that hidden information never leaves the server.

    PYTHONPATH=. python3 tools/visibility_test.py

The renderer choosing not to draw something is not privacy: a client can read
its own socket. This checks the snapshot itself - with every view option off,
the opponent's preparing piece must look idle, its destination must be its own
square, and their mana must be absent entirely.
"""
import math
import pathlib
import re

from server import game, params
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

# A moving piece is visibly moving, so the state is not hidden - but the
# destination still is, and blanking dest_x/dest_y is not enough on its own.
# state_timer on a mover is the time left until it arrives, so position plus
# velocity times timer reconstructs the destination exactly. This used to hand
# it over in full while the marker was "hidden".
q = next(x for x in g.pieces if x.id == "b_pawn_5")
dest = (q.x, q.y + 2.0)
g.queue_move("b_pawn_5", dest, "black")
while q.state != PieceState.MOVING:
    g._tick(0.05)
g._tick(0.05)

seen = look(g.to_dict("white"), "b_pawn_5")
guess = (seen["x"] + seen["vel_x"] * seen["state_timer"],
         seen["y"] + seen["vel_y"] * seen["state_timer"])
miss = math.hypot(guess[0] - dest[0], guess[1] - dest[1])
print(f"white sees black pawn: moving, dest {(seen['dest_x'], seen['dest_y'])}, "
      f"timer {seen['state_timer']}; extrapolates to "
      f"({guess[0]:.2f}, {guess[1]:.2f}), {miss:.2f} short of {dest}")
assert seen["state"] == "moving"
assert (seen["dest_x"], seen["dest_y"]) == (seen["x"], seen["y"])
assert seen["state_timer"] <= game.HIDDEN_TIMER_CAP
assert miss > 0.5, "the destination is still reachable from velocity and timer"
# Its owner keeps the whole truth.
mine = look(g.to_dict("black"), "b_pawn_5")
assert (mine["dest_x"], mine["dest_y"]) == (round(dest[0], 4), round(dest[1], 4))
assert mine["state_timer"] > game.HIDDEN_TIMER_CAP

# VIEW_DEFAULTS is written twice, once per language. The checkboxes are drawn
# from the client's copy and the rules are enforced from the server's, so a
# disagreement means a room quietly plays by settings nobody chose.
block = (pathlib.Path("web/src/settings.ts").read_text()
         .split("export const VIEW_DEFAULTS: View = {")[1].split("};")[0])
client = {k: v == "true" for k, v in re.findall(r"(\w+):\s*(true|false)", block)}
print("client defaults:", client)
assert client == params.VIEW_DEFAULTS, (client, params.VIEW_DEFAULTS)
print("PASS: hidden information is absent from the snapshot")
