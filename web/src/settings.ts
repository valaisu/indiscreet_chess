/**
 * Personal settings. These belong to the person, not the game: they change how
 * this browser reads input and what it draws, and never what the server does.
 * Room rules (tempo, civilizations, what you may see of the opponent) are
 * agreed per room and live in the lobby payloads instead.
 *
 * Stored in localStorage so a phone and a laptop can be set up differently,
 * which is the point - a mouse and a touchscreen do not want the same numbers.
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
  /** Highlight hints and honour any drag distance while held. */
  preciseKey: "Shift" | "Control" | "Alt";
}

export const DEFAULTS: Settings = {
  moveMode: "both",
  dragThreshold: 0.3,
  showHints: true,
  preciseKey: "Shift",
};

/** A drag shorter than this is a click even in precise mode, or every tap moves. */
export const PRECISE_MIN_DRAG = 0.02;

const KEY = "settings";

function load(): Settings {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? { ...DEFAULTS, ...JSON.parse(raw) } : { ...DEFAULTS };
  } catch {
    return { ...DEFAULTS };
  }
}

export const settings: Settings = load();

export function save(patch: Partial<Settings>): void {
  Object.assign(settings, patch);
  try {
    localStorage.setItem(KEY, JSON.stringify(settings));
  } catch {
    // Private mode, or storage disabled. The settings still apply this session.
  }
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
