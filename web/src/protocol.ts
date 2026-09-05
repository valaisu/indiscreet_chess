// Mirrors shared/protocol.py. Keep in sync.
// Must equal VERSION in shared/protocol.py; deploy.sh refuses to ship a mismatch.
export const VERSION = 11;

/** The three tempos a quick match can ask for. */
export const TEMPOS = ["bullet", "rapid", "slow"] as const;

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
export const REMATCH = "REMATCH";
export const RESIGN = "RESIGN";
export const PING = "PING";

export const SIGN_UP = "SIGN_UP";
export const SIGN_IN = "SIGN_IN";
export const SIGN_OUT = "SIGN_OUT";
export const RESUME_SESSION = "RESUME_SESSION";
// Personal settings that follow the account. Only the keys changed while
// signed in travel; the rest stay this device's business.
export const SET_SETTINGS = "SET_SETTINGS";
export const LIST_GAMES = "LIST_GAMES";
export const GET_GAME = "GET_GAME";
export const LIST_ROOMS = "LIST_ROOMS";
export const LIST_ONLINE = "LIST_ONLINE";
export const GET_PROFILE = "GET_PROFILE";

export const ROOM_CREATED = "ROOM_CREATED";
export const ROOM_JOINED = "ROOM_JOINED";
export const ROOM_STATE = "ROOM_STATE";
export const OPPONENT_LEFT = "OPPONENT_LEFT";
export const OPPONENT_REJOINED = "OPPONENT_REJOINED";
export const PONG = "PONG";
export const SERVER_HELLO = "SERVER_HELLO";
export const AUTH_STATE = "AUTH_STATE";
export const AUTH_ERROR = "AUTH_ERROR";
export const RATING_UPDATE = "RATING_UPDATE";
export const GAME_SAVED = "GAME_SAVED";
export const GAME_LIST = "GAME_LIST";
export const GAME_RECORD = "GAME_RECORD";
export const ROOM_LIST = "ROOM_LIST";
export const ONLINE_LIST = "ONLINE_LIST";
export const PROFILE = "PROFILE";

/** One side of a stored game. A null name is an anonymous seat, not a gap. */
export interface GameSide {
  name: string | null;
  civ: string | null;
  /** What they were rated going in, and coming out. Null in an unrated game. */
  rating_before: number | null;
  rating_after: number | null;
}

/** One finished game as the server lists it, without its recording. */
export interface StoredGame {
  id: string;
  at: number;
  /** Which side the player whose page this is was on. */
  seat: string;
  tempo: string;
  winner: string;
  ticks: number;
  rated: boolean;
  unrated_reason: string | null;
  /** Both sides. "The opponent" is whichever one `seat` does not name. */
  players: Record<string, GameSide>;
}

/** One page of stored games. `total` is how many there are in all. */
export interface GamePage {
  games: StoredGame[];
  offset: number;
  total: number;
}

/** One open room, as the "find a game" list shows it. */
export interface OpenRoom {
  code: string;
  tempo: string;
  balanced: boolean;
  base_params: Record<string, Record<string, number>>;
  view: Record<string, boolean>;
  /** Whether a game played here could be rated, from the room's settings
   *  alone: who is sitting in it is not part of the question. */
  rated: boolean;
  /** The host's account name, or null if they are playing anonymously. */
  host: string | null;
  /** Seconds this room has been waiting. */
  waiting: number;
}

/** A player as anyone may see them: their card, and a page of their games. */
export interface PublicProfile extends GamePage {
  id: string;
  name: string;
  ratings: Record<string, { rating: number; games: number }>;
}

/** One seat of a room, as ROOM_STATE describes it. */
export interface SeatCard {
  present: boolean;
  /** The account name, or null when that seat is anonymous or empty. */
  name: string | null;
  /** Their rating at this room's tempo, or null if they have none. */
  rating: { rating: number; games: number } | null;
  ready: boolean;
  /** Whether they have asked for a rematch. Both seats must. */
  rematch: boolean;
}

/** A signed-in player, as the server describes them. */
export interface Identity {
  id: string;
  name: string;
  /** tempo -> rating and games played. A tempo absent here is unplayed. */
  ratings: Record<string, { rating: number; games: number }>;
  /**
   * The settings this account has an opinion about, which override the ones
   * this device holds. A key absent here is not a default: it is no opinion,
   * and the device's own value stands. Absent entirely from an old server.
   */
  settings?: Partial<Settings>;
}

import type { Settings } from "./settings.ts";
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
