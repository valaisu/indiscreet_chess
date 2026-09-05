/**
 * Personal settings. These belong to the person, not the game: they change how
 * this browser reads input and what it draws, and never what the server does.
 * Room rules (tempo, civilizations, what you may see of the opponent) are
 * agreed per room and live in the lobby payloads instead.
 *
 * Two layers, and the rule between them is the whole design:
 *
 *   device   this browser's own values, in localStorage. Written on every
 *            change, signed in or not, so signing out leaves the machine set
 *            up the way its owner set it up.
 *   profile  the signed-in account's values, on the server. Overrides the
 *            device wherever it has a value.
 *
 * A profile holds only the keys that were changed while signed in. An absent
 * key is not a default, it is no opinion: a fresh account does not reset a
 * browser somebody has already set up, and a phone and a laptop keep their own
 * input numbers - a mouse and a touchscreen do not want the same ones - until
 * an account says otherwise.
 */

export interface Settings {
  /** How a move may be issued. Touch devices often want one or the other. */
  moveMode: "both" | "click" | "drag";
  /**
   * How far a drag must travel, in squares, before release counts as a move
   * rather than a click. Below it the piece is merely selected - which is what
   * makes a deliberate 0.2-square move impossible without `precise`.
   */
  dragThreshold: number;
  /** Draw the legal-move wedges for the selected piece. */
  showHints: boolean;
  /**
   * Highlight hints and honour any drag distance while held.
   *
   * A `KeyboardEvent.key`, so a modifier ("Shift") and a letter ("q") are the
   * same kind of value and any key on the keyboard can be bound. Compared
   * with `keyMatches`, never with `===`: a letter arrives capitalised when
   * shift is down.
   */
  preciseKey: string;
  /** Put the selected piece down again. Also a `KeyboardEvent.key`. */
  unselectKey: string;
}

export const DEFAULTS: Settings = {
  moveMode: "both",
  dragThreshold: 0.3,
  showHints: true,
  preciseKey: "Shift",
  unselectKey: "Escape",
};

/**
 * Does this keypress mean that binding?
 *
 * Single characters are compared without case, because the same physical key
 * reports "q" or "Q" depending on whether shift happens to be down - and with
 * the precise key on Shift, that is most of the time.
 */
export function keyMatches(pressed: string, bound: string): boolean {
  if (!bound) return false;
  return pressed.length === 1 && bound.length === 1
    ? pressed.toLowerCase() === bound.toLowerCase()
    : pressed === bound;
}

/** A binding as a player should read it: " " is a key you cannot see. */
export function keyLabel(key: string): string {
  if (key === " ") return "Space";
  if (key === "Escape") return "Esc";
  return key.length === 1 ? key.toUpperCase() : key;
}

/** A drag shorter than this is a click even in precise mode, or every tap moves. */
export const PRECISE_MIN_DRAG = 0.02;

const KEY = "settings";

function loadDevice(): Partial<Settings> {
  try {
    const raw = localStorage.getItem(KEY);
    const value = raw ? JSON.parse(raw) : null;
    return value && typeof value === "object" ? value : {};
  } catch {
    return {};
  }
}

/** This browser's values. */
let device: Partial<Settings> = loadDevice();
/** The signed-in account's values, or null when signed out. */
let profile: Partial<Settings> | null = null;
/** How to send a changed profile to the server. Set once, at boot. */
let publish: ((values: Partial<Settings>) => void) | null = null;

/**
 * The values in force. A live object that the game reads on every frame, so it
 * is rebuilt in place and never reassigned.
 */
export const settings: Settings = { ...DEFAULTS };

function recompute(): void {
  Object.assign(settings, DEFAULTS, device, profile ?? {});
}
recompute();

/** Change a setting. Written to the device, and to the profile if there is one. */
export function save(patch: Partial<Settings>): void {
  Object.assign(device, patch);
  if (profile) Object.assign(profile, patch);
  recompute();
  try {
    localStorage.setItem(KEY, JSON.stringify(device));
  } catch {
    // Private mode, or storage disabled. The settings still apply this session.
  }
  if (profile && publish) publish(profile);
}

/**
 * A sign-in, or a sign-out with null. Nothing is sent back: signing in adopts
 * the account's settings, it does not push this browser's onto the account.
 * That matters on somebody else's machine, and it is why an account with no
 * opinion leaves the device alone rather than overwriting it with defaults.
 *
 * The caller redraws the settings controls: the values in force have changed.
 */
export function applyProfile(values: Partial<Settings> | null): void {
  profile = values;
  recompute();
}

/** Where profile changes go. Registered once there is a socket to send on. */
export function setPublisher(fn: (values: Partial<Settings>) => void): void {
  publish = fn;
}

/** Room-level information rules. Mirrors VIEW_DEFAULTS in server/params.py. */
export interface View {
  enemy_mana: boolean;
  enemy_prep: boolean;
  enemy_cooldown: boolean;
  enemy_dest: boolean;
}

export const VIEW_DEFAULTS: View = {
  enemy_mana: false,
  enemy_prep: true,
  enemy_cooldown: true,
  enemy_dest: false,
};
