/**
 * Game recording and playback.
 *
 * Every GAME_STATE the server sends is already a complete snapshot, so a
 * recording is just the list of them with arrival times — no separate move log
 * to replay against the rules engine, and no chance of the replay disagreeing
 * with what was played. A five-minute game is about 6000 snapshots, which is
 * cheap enough to hold in memory and never touches the server.
 *
 * The recording is what *this* player was sent, so a game with hidden enemy
 * timers replays with them still hidden. That is the honest thing to show.
 */

import type { GameState } from "./protocol.ts";

export interface Frame {
  /** Milliseconds since the first recorded frame. */
  t: number;
  state: GameState;
}

export const SPEEDS = [0.5, 1, 2, 4] as const;

export class Recording {
  frames: Frame[] = [];
  private t0 = 0;

  push(state: GameState, at: number): void {
    if (this.frames.length === 0) this.t0 = at;
    this.frames.push({ t: at - this.t0, state });
  }

  clear(): void {
    this.frames = [];
  }

  get duration(): number {
    return this.frames.length ? this.frames[this.frames.length - 1].t : 0;
  }

  /** Index of the last frame at or before `t`. */
  indexAt(t: number): number {
    let lo = 0;
    let hi = this.frames.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (this.frames[mid].t <= t) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  }
}

/** Playback head over a Recording. Owns only time; the caller renders. */
export class Player {
  t = 0;
  speed = 1;
  playing = true;
  private last = 0;

  constructor(public recording: Recording) {
    this.last = performance.now();
  }

  /** Advance the head and return the frame to draw, plus its age in ms. */
  tick(now: number): { state: GameState; age: number } | null {
    const dt = now - this.last;
    this.last = now;
    if (this.playing) this.seek(this.t + dt * this.speed);
    const frames = this.recording.frames;
    if (!frames.length) return null;
    const i = this.recording.indexAt(this.t);
    return { state: frames[i].state, age: this.t - frames[i].t };
  }

  seek(t: number): void {
    this.t = Math.max(0, Math.min(this.recording.duration, t));
    // Stopping at the end rather than looping: the last frame is the result,
    // and a replay that silently restarts reads as a bug.
    if (this.t >= this.recording.duration) this.playing = false;
  }

  toggle(): void {
    // Pressing play at the end is a request to watch it again.
    if (!this.playing && this.t >= this.recording.duration) this.t = 0;
    this.playing = !this.playing;
  }
}
