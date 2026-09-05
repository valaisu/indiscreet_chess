/**
 * Game recording and playback.
 *
 * Every GAME_STATE the server sends is already a complete snapshot, so a
 * recording is just the list of them with arrival times - no separate move log
 * to replay against the rules engine, and no chance of the replay disagreeing
 * with what was played. A five-minute game is about 6000 snapshots, which is
 * cheap enough to hold in memory and never touches the server.
 *
 * Two things fill a Recording. Live, it is the frames this client was sent,
 * which are only the half of the game its own visibility settings allowed. A
 * stored game is expanded from the server's log instead (expand.ts) and holds
 * everything: both mana pools, both sides' destinations. The replay screen
 * prefers the stored one, so a finished game is watched in full whichever way
 * it was opened - the hiding was a rule of playing, not of watching.
 */

import type { GameState } from "./protocol.ts";

export interface Frame {
  /** Milliseconds since the first recorded frame. */
  t: number;
  state: GameState;
}

/**
 * The speeds the two arrows step between. Zero is in the middle and is what
 * pause means: one number drives playback in both directions, so there is no
 * separate "is it playing" that can disagree with it.
 */
export const SPEEDS = [-4, -1, -0.5, 0, 0.5, 1, 4] as const;

/** The speed to resume at, and where the arrows start. */
export const NORMAL_SPEED = 1;

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
  /** Signed: negative runs the game backwards, zero is paused. */
  speed: number = NORMAL_SPEED;
  /** What pause resumes to. Never zero, so the button always has a way back. */
  private resumeSpeed: number = NORMAL_SPEED;
  private last = 0;

  constructor(public recording: Recording) {
    this.last = performance.now();
  }

  /** Whether the head is moving. Derived, so it cannot disagree with speed. */
  get playing(): boolean {
    return this.speed !== 0;
  }

  /** Advance the head and return the frame to draw, plus its age in ms. */
  tick(now: number): { state: GameState; age: number } | null {
    const dt = now - this.last;
    this.last = now;
    if (this.speed !== 0) this.seek(this.t + dt * this.speed);
    const frames = this.recording.frames;
    if (!frames.length) return null;
    const i = this.recording.indexAt(this.t);
    return { state: frames[i].state, age: this.t - frames[i].t };
  }

  setSpeed(speed: number): void {
    // Asking to run forwards from the end is a request to watch it again;
    // asking to run backwards from the start, likewise from the other end.
    if (speed > 0 && this.t >= this.recording.duration) this.t = 0;
    if (speed < 0 && this.t <= 0) this.t = this.recording.duration;
    this.speed = speed;
    if (speed !== 0) this.resumeSpeed = speed;
  }

  seek(t: number): void {
    this.t = Math.max(0, Math.min(this.recording.duration, t));
    // Stopping at either end rather than looping: the last frame is the
    // result, and a replay that silently restarts reads as a bug. Running
    // backwards, the first frame is the same kind of wall.
    if (this.t >= this.recording.duration && this.speed > 0) this.speed = 0;
    if (this.t <= 0 && this.speed < 0) this.speed = 0;
  }

  /** Pause, or resume at the speed that was last being watched. */
  toggle(): void {
    this.setSpeed(this.speed === 0 ? this.resumeSpeed : 0);
  }
}
