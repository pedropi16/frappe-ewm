import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const { parseReading } = createRequire(import.meta.url)("../../public/js/wms_scale.js");

test("the first number of a line is the weight, comma decimals included", () => {
  assert.equal(parseReading("ST,GS,   12.345 kg"), 12.345);
  assert.equal(parseReading("  7,50"), 7.5);
  assert.equal(parseReading("W -3.2"), -3.2);
});

test("a stable marker keeps unstable readings out", () => {
  const cfg = { scale_stable_marker: "ST" };
  assert.equal(parseReading("US,GS, 12.3 kg", cfg), null);
  assert.equal(parseReading("ST,GS, 12.3 kg", cfg), 12.3);
});

test("a pattern picks the number, and the unit converts to kg", () => {
  assert.equal(parseReading("N 000450 g", { scale_pattern: "N\\s+(\\d+)", scale_unit: "g" }), 0.45);
  assert.equal(parseReading("10 lb", { scale_unit: "lb" }), 4.535924);
});

test("lines without a number, or a broken pattern, give nothing", () => {
  assert.equal(parseReading("OVERLOAD"), null);
  assert.equal(parseReading("12", { scale_pattern: "(" }), null);
  assert.equal(parseReading(null), null);
});
