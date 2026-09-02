/**
 * Tempo presets.
 *
 * TABLE is meant to be hand-edited. One row per parameter, one column per
 * mode, so the modes can be compared and tuned against each other directly.
 * Nothing is derived from anything else — a number here is exactly what the
 * server receives.
 *
 * Modifiers are multiplicative and compose, so a later "civilisation" layer is
 * one more Modifiers map passed to applyModifiers; no structural change here.
 *
 * diameter_piece is deliberately absent. The server applies it per player
 * (server/game.py:37), but the client hardcodes 0.6 in geometry.ts and
 * render.ts and GAME_STATE never sends it, so a mode that changed it would
 * draw the wrong move hints and offer moves the server then rejects. It has to
 * travel in the game state before it can join this table.
 */

export type Params = Record<string, number>;
/** param name -> multiplier applied to the base value. */
export type Modifiers = Record<string, number>;

export const MODES = ["bullet", "rapid", "slow"] as const;

/** Column order matches MODES. Server defaults are in server/params.py. */
const TABLE: Record<string, [number, number, number]> = {
  //                      bullet   rapid    slow      (default)
  mana_refill_rate:      [ 0.35,   0.15,    0.075 ],  // 0.3
  maximum_mana:          [ 5.0,    5.0,     5.0   ],  // 5.0
  base_move_cost:        [ 1.0,    1.0,     1.0   ],  // 1.0
  distance_cost:         [ 0.2,    0.2,     0.2   ],  // 0.2
  preparation_period:    [ 0.35,   0.5,     0.65  ],  // 0.5
  cooldown:              [ 0.7,    1.0,     1.3   ],  // 0.8
  movement_speed:        [ 4.5,    2.0,     1.0   ],  // 4.0
  movement_freedom_deg:  [ 5.0,    5.0,     5.0   ],  // 5.0
};

export const PRESETS: Record<string, Params> = Object.fromEntries(
  MODES.map((mode, col) => [
    mode,
    Object.fromEntries(Object.entries(TABLE).map(([key, row]) => [key, row[col]])),
  ]),
);

export function applyModifiers(base: Params, ...mods: Modifiers[]): Params {
  const out: Params = { ...base };
  for (const mod of mods) {
    for (const [key, factor] of Object.entries(mod)) {
      if (key in out) out[key] = Math.round(out[key] * factor * 1000) / 1000;
    }
  }
  return out;
}

/** Params for a named mode, or null for "custom" (leave the fields alone). */
export function presetParams(mode: string): Params | null {
  const preset = PRESETS[mode];
  return preset ? { ...preset } : null;
}
