/**
 * Civilizations: percentage buffs and debuffs against the base settings.
 *
 * TABLE is the thing to edit. Every number is a percent change from the base
 * civilization ("none"), which is the tempo preset untouched — a column of
 * zeros, and competitive by definition.
 *
 * Balance is checked in points, not percent, because a percent is worth
 * different amounts in different rows: 10% off your cooldown matters more than
 * 10% more max mana. PER_POINT is how much percent buys one point of goodness,
 * taken from improvement_ideas.txt, and its sign is the direction that helps
 * you. Every column must total zero points; unbalanced() reports any that do
 * not. So a row's percent can be read directly, while the columns stay
 * comparable to each other.
 *
 * These stack on a tempo preset: withCiv(PRESETS[mode], civ).
 *
 * Per-piece favourites are not here yet. Params are per player on the server
 * (game.py builds _pp[colour]) and freedom/prep/cooldown reach the client as
 * one value per owner, so a knight cannot yet differ from a rook without
 * changing both. diameter_piece is out for the same reason as in presets.ts.
 */

import type { Modifiers, Params } from "./presets.ts";
import { applyModifiers } from "./presets.ts";

/** Percent that buys one point of goodness. Sign is the helpful direction. */
const PER_POINT: Record<string, number> = {
  mana_refill_rate:       +3,
  maximum_mana:           +5,
  base_move_cost:         -5,
  distance_cost:          -8,
  preparation_period:    -10,
  movement_speed:        +10,
  cooldown:              -10,
  movement_freedom_deg:  +15,
};

export const CIV_NAMES = [
  "hun", "roman", "greek", "persian",
  "egyptian", "norse", "swiss", "byzantine",
] as const;

type Row = [number, number, number, number, number, number, number, number];

/** Percent change from base. Column order matches CIV_NAMES. */
const TABLE: Record<string, Row> = {
  //                       hun  roman  greek  persia  egypt  norse  swiss  byzant     per point
  mana_refill_rate:      [  -6,     0,     0,      0,     0,    -6,     6,      0 ], //   +3
  maximum_mana:          [   0,     0,    -5,      0,    15,    -5,     5,      5 ], //   +5
  base_move_cost:        [   0,   -10,     0,     10,     0,     0,     0,     10 ], //   -5
  distance_cost:         [   0,     0,    16,    -24,     8,     0,     0,      0 ], //   -8
  preparation_period:    [   0,     0,   -10,      0,    20,   -20,    10,     10 ], //  -10
  cooldown:              [  10,   -10,     0,     10,     0,     0,     0,    -20 ], //  -10
  movement_speed:        [  20,   -20,     0,      0,     0,    10,   -20,      0 ], //  +10
  movement_freedom_deg:  [  15,   -15,    30,      0,     0,     0,     0,      0 ], //  +15
};

/** Percent changes per civ, zeros dropped. Exported so the UI can explain a pick. */
export const PERCENTS: Record<string, Record<string, number>> = Object.fromEntries(
  CIV_NAMES.map((civ, col) => [
    civ,
    Object.fromEntries(
      Object.entries(TABLE)
        .map(([key, row]) => [key, row[col]])
        .filter(([, pct]) => pct !== 0),
    ),
  ]),
);

/** What a civ spends, in points. Zero means it is on budget. */
export function points(civ: string): number {
  return Object.entries(PERCENTS[civ] ?? {})
    .reduce((total, [key, pct]) => total + pct / PER_POINT[key], 0);
}

/** Civ names whose points do not balance — empty when the table is sound. */
export function unbalanced(): string[] {
  return CIV_NAMES.filter((civ) => Math.abs(points(civ)) > 1e-9);
}

export const CIVS: Record<string, Modifiers> = Object.fromEntries(
  CIV_NAMES.map((civ) => [
    civ,
    Object.fromEntries(
      Object.entries(PERCENTS[civ]).map(([key, pct]) => [key, 1 + pct / 100]),
    ),
  ]),
);

/** Base params with a civ applied, or the base unchanged for "none". */
export function withCiv(base: Params, civ: string): Params {
  const mods = CIVS[civ];
  return mods ? applyModifiers(base, mods) : base;
}
