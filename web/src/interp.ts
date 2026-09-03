/**
 * Dead reckoning between server ticks. Pieces travel in a straight line at a
 * constant velocity and the server sends position, velocity and the time left
 * to run, so advancing them locally is exact rather than a guess - right up to
 * the moment something interrupts the move.
 *
 * There is deliberately no render delay. A jitter buffer means interpolating
 * between two buffered snapshots; subtracting a delay from an extrapolation
 * just cancels it out. At 20Hz the delay exceeded the tick interval, so dt
 * clamped to zero every frame and pieces jumped between server positions.
 * A late packet needs no cushion here: we keep extrapolating correctly, and
 * the position still agrees when it lands.
 */

import type { GameState } from "./protocol.ts";
import type { Piece } from "./geometry.ts";

export function interpolate(state: GameState, elapsedMs: number): GameState {
  const dt = Math.max(0, elapsedMs / 1000);
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
