// Scale on a serial / USB port, read in the browser through Web Serial (Chrome / Edge): the Repack Center reads the gross weight of an HU from it.
// parseReading() is a pure function (tested under node); ScaleReader wraps the port. Configured per Work Center (scale_* fields).
(function (root) {
  const TO_KG = { kg: 1, g: 0.001, lb: 0.45359237 };

  // One line from the scale -> weight in kg, or null (no number, or not the stable reading the marker asks for).
  function parseReading(line, cfg) {
    cfg = cfg || {};
    const text = String(line == null ? "" : line);
    if (cfg.scale_stable_marker && !text.includes(cfg.scale_stable_marker)) return null;
    let match;
    try { match = text.match(new RegExp(cfg.scale_pattern || "([-+]?\\d+(?:[.,]\\d+)?)")); } catch (e) { return null; }
    if (!match) return null;
    const value = parseFloat(String(match[1] != null ? match[1] : match[0]).replace(",", "."));
    if (!isFinite(value)) return null;
    const kg = value * (TO_KG[cfg.scale_unit || "kg"] || 1);
    return Math.round(kg * 1e6) / 1e6;
  }

  class ScaleReader {
    constructor(cfg) { this.cfg = cfg || {}; this.port = null; this.reader = null; this.buffer = ""; }
    static supported() { return typeof navigator !== "undefined" && "serial" in navigator; }
    async connect() {
      if (this.port) return;
      const c = this.cfg;
      this.port = await navigator.serial.requestPort();
      await this.port.open({ baudRate: c.scale_baud_rate || 9600, dataBits: c.scale_data_bits || 8, parity: c.scale_parity || "none", stopBits: c.scale_stop_bits || 1 });
      const decoder = new TextDecoderStream();
      this.port.readable.pipeTo(decoder.writable).catch(() => {});
      this.reader = decoder.readable.getReader();
    }
    // The next weight (kg) the scale reports, waiting up to timeoutMs; null when none arrived.
    async readOnce(timeoutMs) {
      await this.connect();
      const deadline = Date.now() + (timeoutMs || 4000);
      while (Date.now() < deadline) {
        const left = deadline - Date.now();
        const chunk = await Promise.race([this.reader.read(), new Promise((resolve) => setTimeout(() => resolve({ timeout: true }), left))]);
        if (chunk.timeout || chunk.done) break;
        this.buffer += chunk.value || "";
        const lines = this.buffer.split(/\r\n|\r|\n/);
        this.buffer = lines.pop();
        for (let i = lines.length - 1; i >= 0; i--) {
          const kg = parseReading(lines[i], this.cfg);
          if (kg != null) return kg;
        }
      }
      return null;
    }
    async close() {
      try { if (this.reader) await this.reader.cancel(); if (this.port) await this.port.close(); } catch (e) { /* already closed */ }
      this.port = this.reader = null;
    }
  }

  const api = { parseReading, ScaleReader, supported: ScaleReader.supported };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.WMSScale = api;
})(typeof window !== "undefined" ? window : globalThis);
