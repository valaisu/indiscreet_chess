/**
 * Print every civilization as one table and check it is on budget.
 *
 *   node --experimental-strip-types tools/civ_table.mjs
 *
 * Cells are % against the base civilization, which is all zeros. Rows naming a
 * piece apply to that piece only. The "pt" column is the % that buys one point
 * of goodness, so its sign tells you which direction helps: cooldown is -10,
 * meaning a negative cell is a buff. The last row is what each civ spends, and
 * it must be zero. Exits non-zero if any civ is off budget.
 */
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const src = join(dirname(fileURLToPath(import.meta.url)), "..", "web", "src");
const { PRESETS } = await import(join(src, "presets.ts"));
const { CIV_NAMES, PERCENTS, PIECE_TABLE, PER_POINT, points, unbalanced } =
  await import(join(src, "civs.ts"));

const civs = [...CIV_NAMES];
const LABEL = 26;
const COL = 10;
const pad = (s) => String(s).padStart(COL);

// One row per modifier: the global params first, then any piece-specific ones.
const rows = Object.keys(PRESETS.bullet).map((param) => ({
  label: param,
  rate: PER_POINT[param],
  value: (civ) => PERCENTS[civ][param],
}));

const seen = new Set();
for (const civ of civs) {
  for (const [piece, param] of PIECE_TABLE[civ] ?? []) {
    const key = `${piece} ${param}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({
      label: key,
      rate: PER_POINT[param],
      value: (c) =>
        (PIECE_TABLE[c] ?? []).find(([p, a]) => p === piece && a === param)?.[2],
    });
  }
}

console.log("\n% against base civ. Row's `pt` sign shows which way is better.\n");
console.log("modifier".padEnd(LABEL) + "pt".padStart(4) + civs.map((c) => pad(c)).join(""));
for (const row of rows) {
  const cells = civs.map((civ) => {
    const v = row.value(civ);
    return pad(v === undefined ? "·" : `${v > 0 ? "+" : ""}${v}%`);
  });
  console.log(row.label.padEnd(LABEL) + String(row.rate).padStart(4) + cells.join(""));
}
console.log(
  "points spent".padEnd(LABEL) + "".padStart(4) +
  civs.map((c) => pad(points(c).toFixed(2))).join(""),
);

const bad = unbalanced();
console.log("");
if (bad.length) {
  console.error(`OFF BUDGET: ${bad.map((c) => `${c} = ${points(c)}`).join(", ")}`);
  process.exit(1);
}
console.log(`On budget: all ${civs.length} civilizations spend 0 points.\n`);
