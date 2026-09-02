// Mirrors shared/protocol.py. Keep in sync.
// Must equal VERSION in shared/protocol.py; deploy.sh refuses to ship a mismatch.
export const VERSION = 4;

export const QUEUE_MOVE = "QUEUE_MOVE";
export const GAME_STATE = "GAME_STATE";
export const MOVE_REJECTED = "MOVE_REJECTED";
export const ERROR = "ERROR";

export const CREATE_ROOM = "CREATE_ROOM";
export const JOIN_ROOM = "JOIN_ROOM";
export const QUICK_MATCH = "QUICK_MATCH";
export const REJOIN = "REJOIN";
export const SET_READY = "SET_READY";
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
  /** owner -> piece type -> param overrides. Only what a civ actually changes. */
  piece_params?: Record<string, Record<string, Record<string, number>>>;
  /** Revealed only once the game starts, never during selection. */
  civs?: Record<string, string | null>;
  countdown: number | null;
  game_over: boolean;
  winner: string | null;
}

/**
 * A piece's effective value for `param`. A civilization may single out one
 * piece type, so its override wins over the owner-wide value in `ownerValue`.
 */
export function forPiece(
  state: GameState,
  piece: { owner: string; type: string },
  param: string,
  ownerValue: Record<string, number> | number | undefined,
  fallback: number,
): number {
  const override = state.piece_params?.[piece.owner]?.[piece.type]?.[param];
  return override ?? perOwner(ownerValue, piece.owner, fallback);
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
