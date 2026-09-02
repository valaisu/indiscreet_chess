/**
 * Tempo presets.
 *
 * The three modes differ in one thing: how long you have to react. That is a
 * single scalar `k` applied to the time constants — prep and cooldown stretch
 * with it, speed and mana regen shrink by it, so a slower mode gives the same
 * number of moves spread over more time rather than a different economy.
 * Costs, max mana, freedom and piece size have no time dimension and are left
 * alone.
 *
 * Modifiers are multiplicative and compose, so a later "civilisation" layer is
 * one more Modifiers map passed to applyModifiers — nothing here needs to
 * change to accommodate it.
 */

export type Params = Record<string, number>;
/** param name -> multiplier applied to the base value. */
export type Modifiers = Record<string, number>;

/** Server defaults, mirrored from server/params.py DEFAULT_PARAMS. */
export const BASE: Params = {
  mana_refill_rate: 0.3,
  maximum_mana: 5.0,
  preparation_period: 0.5,
  cooldown: 0.8,
  movement_speed: 4.0,
  movement_freedom_deg: 5.0,
};

/**
 * How much longer everything takes than the base settings. The base is a
 * slightly slow bullet, so bullet itself is a little quicker than it.
 */
export const TEMPO: Record<string, number> = {
  bullet: 0.8,
  rapid: 2.0,
  slow: 4.0,
};

export const MODES = ["bullet", "rapid", "slow"] as const;

function tempoModifiers(k: number): Modifiers {
  return {
    preparation_period: k,
    cooldown: k,
    movement_speed: 1 / k,
    mana_refill_rate: 1 / k,
  };
}

// Three decimals keeps the number inputs readable; every param is well clear
// of the server's limits at these scales.
const round = (v: number) => Math.round(v * 1000) / 1000;

export function applyModifiers(base: Params, ...mods: Modifiers[]): Params {
  const out: Params = { ...base };
  for (const mod of mods) {
    for (const [key, factor] of Object.entries(mod)) {
      if (key in out) out[key] = round(out[key] * factor);
    }
  }
  return out;
}

/** Params for a named mode, or null for "custom" (leave the fields alone). */
export function presetParams(mode: string): Params | null {
  const k = TEMPO[mode];
  return k === undefined ? null : applyModifiers(BASE, tempoModifiers(k));
}
