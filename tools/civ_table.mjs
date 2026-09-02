/**
 * Print every civilization as a table and check it is on budget.
 *
 *   node --experimental-strip-types tools/civ_table.mjs
 *
 * Percentages are against the base civilization, which is a column of zeros.
 * Points are the common currency that makes rows comparable: 10% off a
 * cooldown is worth more than 10% more max mana, and a per-piece effect is
 * discounted by how often that piece actually moves. Every civ should total
 * zero. Exits non-zero if any does not.
 */
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const src = join(dirname(fileURLToPath(import.meta.url)), "..", "web", "src");
const { PRESETS } = await import(join(src, "presets.ts"));
const {
  CIV_NAMES, PERCENTS, PIECE_TABLE, PER_POINT,
  globalPoints, piecePoints, points, unbalanced,
} = await import(join(src, "civs.ts"));

const civs = [...CIV_NAMES];
const cell = (s) => String(s).padStart(11);
const num = (v) => (Math.round(v * 1000) / 1000).toFixed(2);

console.log("\nGlobal modifiers — % against the base civilization\n");
console.log("param".padEnd(22) + civs.map(cell).join(""));
for (const key of Object.keys(PRESETS.bullet)) {
  const row = civs.map((c) => {
    const v = PERCENTS[c][key];
    return cell(v === undefined ? "·" : `${v > 0 ? "+" : ""}${v}%`);
  });
  console.log(key.padEnd(22) + row.join(""));
}

console.log("\nPer-piece effects\n");
const rows = civs.flatMap((civ) =>
  (PIECE_TABLE[civ] ?? []).map(([piece, param, pct]) => ({ civ, piece, param, pct })));
if (rows.length === 0) console.log("  (none)");
for (const { civ, piece, param, pct } of rows) {
  const helps = pct / PER_POINT[param] > 0;
  console.log(
    `  ${civ.padEnd(11)}${piece.padEnd(8)}${param.padEnd(22)}` +
    `${((pct > 0 ? "+" : "") + pct + "%").padStart(7)}   ${helps ? "buff" : "debuff"}`,
  );
}

console.log("\nBudget — points spent, must total zero\n");
console.log("civ".padEnd(13) + "global".padStart(9) + "per-piece".padStart(11) + "total".padStart(9));
for (const civ of civs) {
  console.log(
    civ.padEnd(13) + num(globalPoints(civ)).padStart(9) +
    num(piecePoints(civ)).padStart(11) + num(points(civ)).padStart(9),
  );
}
console.log("base".padEnd(13) + num(0).padStart(9) + num(0).padStart(11) + num(0).padStart(9));

const bad = unbalanced();
console.log("");
if (bad.length) {
  console.error(`OFF BUDGET: ${bad.map((c) => `${c} = ${points(c)}`).join(", ")}`);
  process.exit(1);
}
console.log(`On budget: all ${civs.length} civilizations total 0 points.\n`);
