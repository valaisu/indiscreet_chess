/**
 * Signed-in identity, client side.
 *
 * The server owns the truth; this holds the session token between visits and
 * whatever the server last said about who we are. Nothing here decides
 * anything: it asks, and re-renders on the answer.
 *
 * The token is the credential, so it lives in localStorage rather than in a
 * variable - a reload must not sign you out - and it is cleared on every
 * failure to resume, so a revoked session does not linger as a token that
 * quietly never works.
 */

import type { Identity } from "./protocol.ts";
import * as P from "./protocol.ts";

const TOKEN_KEY = "session";

/** Null when signed out. Read it; do not write it. */
export let identity: Identity | null = null;

export function token(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null; // private mode: sign-in still works, it just will not persist
  }
}

function keepToken(value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(TOKEN_KEY);
    else localStorage.setItem(TOKEN_KEY, value);
  } catch {
    /* nothing to do; the session lasts this page load */
  }
}

/** Apply an AUTH_STATE from the server. Returns the new identity. */
export function applyAuthState(msg: { user: (Identity & { token?: string }) | null }):
  Identity | null {
  if (msg.user) {
    if (msg.user.token) keepToken(msg.user.token);
    const { token: _drop, ...rest } = msg.user;
    identity = rest as Identity;
  } else {
    // Signed out, or a token the server would not honour. Either way the
    // stored one is now worthless and keeping it only makes the next visit
    // fail the same way.
    keepToken(null);
    identity = null;
  }
  return identity;
}

export function signUp(send: (m: object) => void, name: string, password: string): void {
  send({ type: P.SIGN_UP, name, password });
}

export function signIn(send: (m: object) => void, name: string, password: string): void {
  send({ type: P.SIGN_IN, name, password });
}

export function signOut(send: (m: object) => void): void {
  send({ type: P.SIGN_OUT, token: token() });
  keepToken(null);
  identity = null;
}

/** Offer a stored token, if there is one. Safe to call on every connect. */
export function resume(send: (m: object) => void): void {
  const t = token();
  if (t) send({ type: P.RESUME_SESSION, token: t });
}

/** "1216 (12 games)" for one tempo, or null when it has never been played. */
export function ratingLabel(tempo: string): string | null {
  const r = identity?.ratings?.[tempo];
  if (!r) return null;
  return `${Math.round(r.rating)} (${r.games} game${r.games === 1 ? "" : "s"})`;
}
