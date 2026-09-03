/**
 * One line-drawn icon per civilization, inline so nothing is fetched.
 *
 * They are strokes in `currentColor` on a 24x24 grid: the card sets the colour
 * and the size, and a picked card can brighten its icon with no second asset.
 * Emoji were the cheaper option and are the wrong one - the pause glyph in the
 * replay bar already rendered as tofu on one machine, and a civilization is
 * picked from a row of eight where a missing glyph is not recoverable.
 */

const svg = (body: string) =>
  `<svg class="civ-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" ` +
  `stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" ` +
  `aria-hidden="true">${body}</svg>`;

export const CIV_ICONS: Record<string, string> = {
  // A bare ring: the board as it comes.
  none: svg(`<circle cx="12" cy="12" r="7.5"/>`),

  // Bow drawn, arrow nocked.
  hun: svg(
    `<path d="M8 4 C15 8 15 16 8 20"/><path d="M8 4 L8 20"/>` +
    `<path d="M4 12 H19"/><path d="M15 9 L19 12 L15 15"/>`,
  ),

  // The scutum: rectangular, with its spine and boss.
  roman: svg(
    `<rect x="5" y="4" width="14" height="16" rx="1.8"/>` +
    `<path d="M12 4 V9.5"/><path d="M12 14.5 V20"/>` +
    `<circle cx="12" cy="12" r="2.2"/>`,
  ),

  // A fluted column on its plinth.
  greek: svg(
    `<path d="M5 5 H19"/><path d="M5 17 H19"/><path d="M3.5 19.5 H20.5"/>` +
    `<path d="M7.5 5 V17"/><path d="M12 5 V17"/><path d="M16.5 5 V17"/>`,
  ),

  // The royal road, running to the horizon. A winged disc was the first try
  // and reads as a bat.
  persian: svg(
    `<path d="M3.5 20.5 L10 4"/><path d="M20.5 20.5 L14 4"/>` +
    `<path d="M12 6.5 V9"/><path d="M12 11.5 V14"/><path d="M12 16.5 V19"/>`,
  ),

  // A pyramid, one face lit.
  egyptian: svg(
    `<path d="M2.5 19.5 H21.5 L12 4 Z"/><path d="M12 4 L7.5 19.5"/>`,
  ),

  // The hammer, short-hafted. Two earlier drawings failed the size test: a
  // longship reads as a cup with a cross in it, and an axe blade hanging off
  // one side of its haft reads as a pennant on a flagpole.
  norse: svg(
    `<rect x="4.5" y="3.5" width="15" height="6.5" rx="1.2"/>` +
    `<path d="M9.8 10 H14.2 V20.5 H9.8 Z"/>`,
  ),

  // Crossed pikes, blades up.
  swiss: svg(
    `<path d="M5.5 18.5 L17.5 6.5"/><path d="M15.8 5.2 L19.5 4.5 L18.8 8.2 Z"/>` +
    `<path d="M18.5 18.5 L6.5 6.5"/><path d="M8.2 5.2 L4.5 4.5 L5.2 8.2 Z"/>`,
  ),

  // A dome behind the land walls.
  byzantine: svg(
    `<path d="M6 12 A6 6 0 0 1 18 12"/><path d="M4.5 12 H19.5"/>` +
    `<path d="M6 12 V20"/><path d="M18 12 V20"/>` +
    `<path d="M9.5 20 V16.8 A2.5 2.5 0 0 1 14.5 16.8 V20"/>` +
    `<path d="M3 20 H21"/>`,
  ),
};
