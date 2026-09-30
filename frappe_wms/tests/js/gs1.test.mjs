import test from "node:test";
import assert from "node:assert/strict";
import { parseGS1, gtinVariants, gs1Element } from "../../public/js/wms_rf/core/gs1.js";

const GS = "\u001d";

test("GS1-128 with FNC1 separators: GTIN, expiry, batch, count, serial", () => {
  assert.deepEqual(parseGS1(`0109501101530003172501311012345${GS}3024`),
    { gtin: "09501101530003", expiry: "2025-01-31", batch: "12345", count: 24 });
  assert.deepEqual(parseGS1(`]C101095011015300032112AB${GS}10L7`), { gtin: "09501101530003", serial: "12AB", batch: "L7" });
});

test("variable-length field last needs no separator; expiry day 00 is end of month", () => {
  assert.deepEqual(parseGS1("010950110153000317240200"), { gtin: "09501101530003", expiry: "2024-02-29" });
  assert.deepEqual(parseGS1("0109501101530003" + "10BATCH-9"), { gtin: "09501101530003", batch: "BATCH-9" });
});

test("human-readable parentheses form and SSCC", () => {
  assert.deepEqual(parseGS1("(00)095011015300000011"), { sscc: "095011015300000011" });
  assert.deepEqual(parseGS1("(01)09501101530003(10)ABC(21)S-1"), { gtin: "09501101530003", batch: "ABC", serial: "S-1" });
});

test("ordinary codes are not GS1", () => {
  for (const code of ["MAD1-BULK-A-01-01", "SKU-00001", "HU-00000147", "10-A-01", "0123456", "5901234123457", "21", ""]) {
    assert.equal(parseGS1(code), null, code);
  }
});

test("gtinVariants matches a stored EAN-13 against a printed GTIN-14", () => {
  assert.ok(gtinVariants("05901234123457").includes("5901234123457"));
  assert.deepEqual(gtinVariants("SKU-1"), ["SKU-1"]);
});

test("gs1Element picks what a field is after", () => {
  const label = "(00)095011015300000011(01)09501101530003(10)LOT7(21)SN9";
  assert.equal(gs1Element(label), "095011015300000011", "SSCC first: the label is a pallet");
  assert.equal(gs1Element(label, "gtin"), "09501101530003");
  assert.equal(gs1Element(label, "batch"), "LOT7");
  assert.equal(gs1Element(label, "serial"), "SN9");
  assert.equal(gs1Element("(01)09501101530003(10)B1"), "09501101530003");
  assert.equal(gs1Element("MAD1-BULK-A-01"), null);
});
