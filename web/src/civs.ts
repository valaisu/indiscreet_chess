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
 * PIECE_TABLE singles out one piece type. Such an effect is worth only the
 * share of moves that piece accounts for, so its point cost is scaled by
 * WEIGHT — a 20% discount on the king, which rarely moves, costs a fraction of
 * the same discount on pawns. Buffing the king or pawns is defensive; buffing
 * anything else is offensive.
 *
 * diameter_piece is out for the same reason as in presets.ts.
 */

import type { Modifiers, Params } from "./presets.ts";
import { applyModifiers } from "./presets.ts";

/** Percent that buys one point of goodness. Sign is the helpful direction. */
export const PER_POINT: Record<string, number> = {
  mana_refill_rate:       +3,
  maximum_mana:           +5,
  base_move_cost:         -5,
  distance_cost:          -8,
  preparation_period:    -10,
  movement_speed:        +10,
  cooldown:              -10,
  movement_freedom_deg:  +15,
};

/**
 * Share of the moves each piece type accounts for while the game is still
 * undecided — not its chess value. Eight pawns move constantly; the queen is
 * powerful but held back; the king barely moves at all. These are estimates
 * and the honest thing to tune once games get played.
 */
const WEIGHT: Record<string, number> = {
  pawn: 0.30, knight: 0.20, bishop: 0.16, rook: 0.14, queen: 0.12, king: 0.08,
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
  preparation_period:    [   0,     0,   -10,      0,    24,   -20,    10,   12.4], //  -10
  cooldown:              [  13,   -10,     0,   13.5,     0,     0,     0,    -20 ], //  -10
  movement_speed:        [  20,   -26,     0,      0,     0,    10, -20.5,      0 ], //  +10
  movement_freedom_deg:  [  15,   -15,  34.2,      0,     0,     0,     0,      0 ], //  +15
};

/** [piece type, param, percent] per civ. Not every civ has one. */
export const PIECE_TABLE: Record<string, [string, string, number][]> = {
  hun:       [["knight", "cooldown",           -15]],  // steppe cavalry
  roman:     [["pawn",   "base_move_cost",     -10]],  // the legion is the army
  greek:     [["rook",   "movement_speed",     -20]],  // no tradition of siege
  persian:   [["rook",   "distance_cost",      -20]],  // chariots on open roads
  egyptian:  [["king",   "base_move_cost",     -25]],  // the pharaoh is the state
  norse:     [],
  swiss:     [["pawn",   "cooldown",           -15],   // pikemen, endlessly
              ["knight", "preparation_period", +20]],  // and famously no horse
  byzantine: [["king",   "preparation_period", -30]],  // the emperor behind walls
};

const round = (v: number) => Math.round(v * 1000) / 1000;

/** Absolute per-piece overrides for the payload, derived from resolved params. */
export function piecePayload(base: Params, civ: string): Record<string, Record<string, number>> {
  const out: Record<string, Record<string, number>> = {};
  for (const [piece, param, pct] of PIECE_TABLE[civ] ?? []) {
    if (base[param] === undefined) continue;
    (out[piece] ??= {})[param] = round(base[param] * (1 + pct / 100));
  }
  return out;
}

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

/** Points spent by the modifiers that apply to every piece. */
export function globalPoints(civ: string): number {
  return Object.entries(PERCENTS[civ] ?? {})
    .reduce((total, [key, pct]) => total + pct / PER_POINT[key], 0);
}

/** Points spent singling out piece types, discounted by how often they move. */
export function piecePoints(civ: string): number {
  return (PIECE_TABLE[civ] ?? []).reduce(
    (total, [piece, param, pct]) => total + (pct / PER_POINT[param]) * WEIGHT[piece],
    0,
  );
}

/** What a civ spends in total. Zero means on budget. */
export function points(civ: string): number {
  return round(globalPoints(civ) + piecePoints(civ));
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
