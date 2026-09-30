import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const S = require("../../public/js/wms_selection.js");

test("shortcut syntax: patterns, operators, ranges, lists, excludes, blank", () => {
  assert.deepEqual(S.parseShortcut("SKU-1*", "Link"), { include: [{ op: "cp", low: "SKU-1*" }], exclude: [] });
  assert.deepEqual(S.parseShortcut(">=10", "Float"), { include: [{ op: "ge", low: "10" }], exclude: [] });
  assert.deepEqual(S.parseShortcut("<>X", "Data").include, [{ op: "ne", low: "X" }]);
  assert.deepEqual(S.parseShortcut("!=X", "Data").include, [{ op: "ne", low: "X" }]);
  assert.deepEqual(S.parseShortcut("10..20", "Int").include, [{ op: "bt", low: "10", high: "20" }]);
  assert.deepEqual(S.parseShortcut("a; b ;c", "Data").include.map((r) => r.low), ["a", "b", "c"]);
  assert.deepEqual(S.parseShortcut("A*;!A-01;!A-05..A-07", "Data"), {
    include: [{ op: "cp", low: "A*" }],
    exclude: [{ op: "eq", low: "A-01" }, { op: "bt", low: "A-05", high: "A-07" }],
  });
  assert.deepEqual(S.parseShortcut("=", "Link").include, [{ op: "eq", low: "" }]);
  assert.equal(S.parseShortcut("  ", "Data"), null);
  // numbers and dates never become patterns
  assert.deepEqual(S.parseShortcut("1+2", "Float").include, [{ op: "eq", low: "1+2" }]);
});

test("pasted Excel column becomes one value per line (tabs too)", () => {
  const c = S.parsePasted("SKU-1\r\nSKU-2\n\nSKU-3\t!SKU-4\n", "Link");
  assert.deepEqual(c.include.map((r) => r.low), ["SKU-1", "SKU-2", "SKU-3"]);
  assert.deepEqual(c.exclude.map((r) => r.low), ["SKU-4"]);
});

test("formatShortcut round-trips what the syntax can express and refuses what it cannot", () => {
  for (const text of ["A*", ">=10", "<>X", "1..5", "a;b;c", "A*;!A-01;!A-05..A-07", "="]) {
    const ft = /^[<>0-9]/.test(text) ? "Float" : "Data";
    assert.equal(S.formatShortcut(S.parseShortcut(text, ft), ft), text, text);
  }
  assert.equal(S.formatShortcut({ include: [{ op: "eq", low: "a;b" }] }, "Data"), null);
  assert.equal(S.formatShortcut({ include: [{ op: "nb", low: 1, high: 2 }] }, "Int"), null);
  assert.equal(S.formatShortcut({ exclude: [{ op: "gt", low: 1 }] }, "Int"), null);
  assert.equal(S.formatShortcut({ include: [{ op: "eq", low: "A*" }] }, "Data"), null, "a literal star cannot be typed");
  assert.equal(S.formatShortcut(undefined, "Data"), "");
  assert.equal(S.countOf({ include: [1, 2], exclude: [3] }), 3);
});
