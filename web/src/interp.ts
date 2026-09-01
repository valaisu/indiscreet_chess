/**
 * Dead reckoning between server ticks, with a render delay so ordinary jitter
 * does not show up as stutter: we draw the world slightly in the past and let
 * the next packet arrive before it is needed.
 */

import type { GameState } from "./protocol.ts";
import type { Piece } from "./geometry.ts";

export const RENDER_DELAY_MS = 100;

export function interpolate(state: GameState, elapsedMs: number): GameState {
  const dt = Math.max(0, (elapsedMs - RENDER_DELAY_MS) / 1000);
  const pieces: Piece[] = state.pieces.map((p) => {
    const timer = p.state_timer ?? 0;
    if (p.state === "moving") {
      const step = Math.min(dt, timer);
      return {
        ...p,
        x: p.x + (p.vel_x ?? 0) * step,
        y: p.y + (p.vel_y ?? 0) * step,
        state_timer: Math.max(0, timer - dt),
      };
    }
    if (p.state === "preparation" || p.state === "cooldown") {
      return { ...p, state_timer: Math.max(0, timer - dt) };
    }
    return p;
  });
  return { ...state, pieces };
}
