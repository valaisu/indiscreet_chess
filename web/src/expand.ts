/**
 * Expand a stored event log back into GAME_STATE snapshots.
 *
 * The counterpart to server/recorder.py. A recording holds only the moments
 * something changed; everything between two events is a straight line, so
 * replaying is: run each piece's timer and velocity forward one tick, then let
 * any event recorded for that tick overwrite the result.
 *
 * Nothing here knows the rules. It does not decide when a preparation ends, who
 * captures whom, or where a collision stops a piece: every one of those is an
 * event the server already wrote down. That is the point of storing effects
 * rather than moves - this file cannot disagree with the game that was played,
 * and rebalancing the physics does not rot the games already stored.
 *
 * The output is the same shape the server broadcasts live, so the existing
 * Recording, Player and Renderer take it unchanged.
 */

import type { GameState } from "./protocol.ts";

/** Event format this file understands. Mirrors FORMAT in server/recorder.py. */
export const FORMAT = 1;

export interface LogEvent {
  k: string;
  t: number;
  [field: string]: unknown;
}

export interface LogHeader {
  format: number;
  tick_rate: number;
  civs: Record<string, string | null>;
  solo: boolean;
  max_mana: Record<string, number>;
  refill: Record<string, number>;
  freedom_deg: Record<string, number>;
  prep_period: Record<string, number>;
  cooldown: Record<string, number>;
  player_params: Record<string, { base_move_cost: number; distance_cost: number }>;
  piece_params: Record<string, Record<string, Record<string, number>>>;
  pieces: { id: string; ty: string; o: string; x: number; y: number; d: number }[];
  mana: Record<string, number>;
}

export interface GameLog {
  header: LogHeader;
  events: LogEvent[];
}

/** A piece as the expander tracks it: the server's own fields, unrounded. */
interface Live {
  id: string;
  type: string;
  owner: string;
  x: number;
  y: number;
  state: string;
  state_timer: number;
  dest_x: number;
  dest_y: number;
  vel_x: number;
  vel_y: number;
  has_moved: boolean;
  d: number;
}

// Must round exactly as Python's round(v, n) does, or an expanded frame differs
// from the broadcast one in the last decimal place. Multiplying by a power of
// ten first does not: v * 1e4 carries its own error, which pushes values across
// the boundary. toFixed rounds the double's exact decimal value, as Python
// does, and a true tie cannot occur - a decimal ending in 5 at the fifth place
// is not representable as a binary fraction.
const r4 = (v: number) => Number(v.toFixed(4));
const r3 = (v: number) => Number(v.toFixed(3));

export class ExpandError extends Error {}

/**
 * Every tick of a recorded game, oldest first.
 *
 * A five minute game is about 6000 frames, which is exactly what the client
 * already holds in memory while playing one, so this is no more expensive than
 * watching the game live was.
 */
export function expand(log: GameLog): GameState[] {
  const h = log.header;
  if (h.format !== FORMAT) {
    throw new ExpandError(
      `recording is format ${h.format}, this build reads format ${FORMAT}`);
  }
  const dt = 1 / h.tick_rate;

  const live = new Map<string, Live>();
  for (const p of h.pieces) {
    live.set(p.id, {
      id: p.id, type: p.ty, owner: p.o, x: p.x, y: p.y,
      state: "idle", state_timer: 0,
      // The opening board leaves dest at the dataclass default rather than at
      // the piece's own square, and the live snapshot shows that. Matching it
      // keeps an expanded frame identical to the one that was broadcast.
      dest_x: 0, dest_y: 0,
      vel_x: 0, vel_y: 0, has_moved: false, d: p.d,
    });
  }
  const mana: Record<string, number> = { ...h.mana };

  // Events grouped by the tick they were observed at. They describe the state
  // at the *end* of that tick, so they are applied after it has been advanced.
  const byTick = new Map<number, LogEvent[]>();
  let lastTick = 0;
  for (const e of log.events) {
    let bucket = byTick.get(e.t);
    if (!bucket) byTick.set(e.t, bucket = []);
    bucket.push(e);
    if (e.t > lastTick) lastTick = e.t;
  }

  const frames: GameState[] = [];
  let gameOver = false;
  let winner: string | null = null;

  for (let tick = 0; tick <= lastTick; tick++) {
    if (tick > 0) {
      for (const c of ["white", "black"]) {
        mana[c] = Math.min(mana[c] + h.refill[c] * dt, h.max_mana[c]);
      }
      for (const p of live.values()) {
        if (p.state === "moving") {
          p.x += p.vel_x * dt;
          p.y += p.vel_y * dt;
          p.state_timer -= dt;
        } else if (p.state_timer > 0) {
          p.state_timer = Math.max(0, p.state_timer - dt);
        }
      }
    }

    for (const e of byTick.get(tick) ?? []) {
      switch (e.k) {
        case "+": {
          live.set(e.id as string, {
            id: e.id as string, type: e.ty as string, owner: e.o as string,
            x: e.x as number, y: e.y as number,
            state: "idle", state_timer: 0, dest_x: 0, dest_y: 0,
            vel_x: 0, vel_y: 0, has_moved: false, d: e.d as number,
          });
          break;
        }
        case "-":
          live.delete(e.id as string);
          break;
        case "s": {
          const p = live.get(e.id as string);
          if (!p) break;
          p.state = e.st as string;
          p.x = e.x as number;
          p.y = e.y as number;
          p.dest_x = e.dx as number;
          p.dest_y = e.dy as number;
          p.vel_x = e.vx as number;
          p.vel_y = e.vy as number;
          p.state_timer = e.tm as number;
          // Read, never inferred from the state. A piece whose preparation
          // ends and whose move finishes inside one tick is never observed in
          // "moving" at all, and inferring the flag from the state missed it.
          p.has_moved = e.hm as boolean;
          break;
        }
        case "y": {
          const p = live.get(e.id as string);
          if (!p) break;
          p.type = e.ty as string;
          p.d = e.d as number;
          break;
        }
        case "m":
          mana.white = e.white as number;
          mana.black = e.black as number;
          break;
        case "e":
          gameOver = true;
          winner = e.winner as string;
          break;
      }
    }

    frames.push({
      type: "GAME_STATE",
      tick,
      pieces: [...live.values()].map((p) => ({
        id: p.id,
        type: p.type,
        owner: p.owner,
        x: r4(p.x),
        y: r4(p.y),
        state: p.state,
        state_timer: r4(p.state_timer),
        dest_x: r4(p.dest_x),
        dest_y: r4(p.dest_y),
        vel_x: r4(p.vel_x),
        vel_y: r4(p.vel_y),
        has_moved: p.has_moved,
        d: r4(p.d),
      })) as GameState["pieces"],
      mana: { white: r3(mana.white), black: r3(mana.black) },
      max_mana: h.max_mana,
      freedom_deg: h.freedom_deg,
      prep_period: h.prep_period,
      cooldown: h.cooldown,
      player_params: h.player_params,
      piece_params: h.piece_params,
      civs: h.civs,
      countdown: null,
      game_over: gameOver,
      winner,
    });
  }

  return frames;
}
