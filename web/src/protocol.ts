// Mirrors shared/protocol.py. Keep in sync.
// Must equal VERSION in shared/protocol.py; deploy.sh refuses to ship a mismatch.
export const VERSION = 1;

export const QUEUE_MOVE = "QUEUE_MOVE";
export const GAME_STATE = "GAME_STATE";
export const MOVE_REJECTED = "MOVE_REJECTED";
export const ERROR = "ERROR";

export const CREATE_ROOM = "CREATE_ROOM";
export const JOIN_ROOM = "JOIN_ROOM";
export const QUICK_MATCH = "QUICK_MATCH";
export const REJOIN = "REJOIN";
export const LEAVE_ROOM = "LEAVE_ROOM";
export const PING = "PING";

export const ROOM_CREATED = "ROOM_CREATED";
export const ROOM_JOINED = "ROOM_JOINED";
export const ROOM_STATE = "ROOM_STATE";
export const OPPONENT_LEFT = "OPPONENT_LEFT";
export const OPPONENT_REJOINED = "OPPONENT_REJOINED";
export const PONG = "PONG";
export const SERVER_HELLO = "SERVER_HELLO";

import type { Piece } from "./geometry.ts";

export interface GameState {
  type: string;
  tick: number;
  pieces: Piece[];
  mana: Record<string, number>;
  max_mana: Record<string, number>;
  freedom_deg: Record<string, number> | number;
  prep_period: Record<string, number> | number;
  cooldown: Record<string, number> | number;
  player_params?: Record<string, { base_move_cost: number; distance_cost: number }>;
  countdown: number | null;
  game_over: boolean;
  winner: string | null;
}

/** Read a field that the server sends either per-owner or as a bare number. */
export function perOwner(
  value: Record<string, number> | number | undefined,
  owner: string,
  fallback: number,
): number {
  if (typeof value === "number") return value;
  if (value && typeof value === "object") return value[owner] ?? fallback;
  return fallback;
}
