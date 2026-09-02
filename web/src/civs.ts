/**
 * Civilizations: a balance budget spent across the parameter categories.
 *
 * UNIT is one "point of goodness" per category, taken from improvement_ideas.txt
 * — the sign already points the good way, so +1 point always helps and -1 always
 * hurts. A civ is a column of points, and every column must sum to zero, which
 * is what keeps them competitive with each other. check() enforces that.
 *
 * These stack on top of a tempo preset: applyModifiers(PRESETS[mode], CIV[name]).
 *
 * Per-piece favourites are not here yet. Params are per player on the server
 * (game.py builds _pp[colour]) and freedom/prep/cooldown reach the client as one
 * value per owner, so a knight cannot yet differ from a rook without changing
 * both. diameter_piece is left out for the same reason as in presets.ts.
 */

import type { Modifiers, Params } from "./presets.ts";
import { applyModifiers } from "./presets.ts";

/** Fractional change per point. Sign is the direction that helps you. */
const UNIT: Record<string, number> = {
  mana_refill_rate:      +0.03,
  maximum_mana:          +0.05,
  base_move_cost:        -0.05,
  distance_cost:         -0.08,
  preparation_period:    -0.10,
  movement_speed:        +0.10,
  cooldown:              -0.10,
  movement_freedom_deg:  +0.15,
};

export const CIV_NAMES = ["mongol", "roman", "egyptian", "greek", "persian"] as const;

/** Points per civ. Column order matches CIV_NAMES. Each column must total 0. */
const TABLE: Record<string, [number, number, number, number, number]> = {
  //                      mongol  roman  egypt  greek  persia
  mana_refill_rate:      [  -2,     0,     1,     0,     1  ],
  maximum_mana:          [   0,     0,     2,    -1,     0  ],
  base_move_cost:        [   0,     2,     0,     0,    -2  ],
  distance_cost:         [   0,     0,    -1,    -2,     2  ],
  preparation_period:    [   0,     0,    -2,     1,     0  ],
  cooldown:              [  -1,     1,     0,     0,    -1  ],
  movement_speed:        [   2,    -2,     0,     0,     0  ],
  movement_freedom_deg:  [   1,    -1,     0,     2,     0  ],
};

/** Points spent, by civ. Exported so the UI can explain a pick. */
export const POINTS: Record<string, Record<string, number>> = Object.fromEntries(
  CIV_NAMES.map((civ, col) => [
    civ,
    Object.fromEntries(
      Object.entries(TABLE)
        .map(([key, row]) => [key, row[col]])
        .filter(([, points]) => points !== 0),
    ),
  ]),
);

export const CIVS: Record<string, Modifiers> = Object.fromEntries(
  CIV_NAMES.map((civ) => [
    civ,
    Object.fromEntries(
      Object.entries(POINTS[civ]).map(([key, pts]) => [key, 1 + UNIT[key] * pts]),
    ),
  ]),
);

/** Civ names whose points do not balance — empty when the table is sound. */
export function unbalanced(): string[] {
  return CIV_NAMES.filter(
    (civ) => Object.values(POINTS[civ]).reduce((a, b) => a + b, 0) !== 0,
  );
}

/** Base params with a civ applied, or the base unchanged for "none". */
export function withCiv(base: Params, civ: string): Params {
  const mods = CIVS[civ];
  return mods ? applyModifiers(base, mods) : base;
}
