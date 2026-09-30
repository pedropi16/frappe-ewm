import { h } from "#wms/ui/dom.js";
import { _ } from "#wms/core/i18n.js";
import { normalizeScan } from "#wms/core/scan.js";
import { feedback } from "#wms/core/feedback.js";

// Phone-camera barcode scanning. Chrome/Android has the native BarcodeDetector; iOS Safari does not, so the vendored
// ZXing build is loaded on first use (336 KB, only when someone actually taps the camera button).
const FORMATS = ["code_128", "code_39", "code_93", "ean_13", "ean_8", "upc_a", "upc_e", "itf", "codabar", "qr_code", "data_matrix"];

let zxingLoading = null;
function loadZXing() {
  if (window.ZXing) return Promise.resolve(window.ZXing);
  if (zxingLoading) return zxingLoading;
  zxingLoading = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = (window.WMS && window.WMS.zxingUrl) || "/assets/frappe_wms/js/wms_rf/vendor/zxing-library.min.js";
    s.onload = () => resolve(window.ZXing);
    s.onerror = () => { zxingLoading = null; reject(new Error(_("The camera scanner could not be loaded. Check your connection."))); };
    document.head.appendChild(s);
  });
  return zxingLoading;
}

export function cameraSupported() {
  return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) && window.isSecureContext;
}

function cameraError(e) {
  const n = e && e.name;
  if (n === "NotAllowedError" || n === "SecurityError") return _("Camera access was blocked. Allow the camera for this site in your browser settings, or type the code instead.");
  if (n === "NotFoundError" || n === "OverconstrainedError") return _("No camera was found on this device.");
  if (n === "NotReadableError") return _("The camera is in use by another app.");
  return (e && e.message) || _("The camera could not be started.");
}

// Opens a full-screen camera view. Resolves with the scanned text, or null if the operator cancelled / it failed
// (failure is shown inside the overlay, so the caller only needs to handle "got a code" vs "nothing").
export function scanWithCamera({ hint } = {}) {
  return new Promise((resolve) => {
    let stream = null, stopped = false, raf = 0, zxTimer = 0, torchOn = false;
    const video = h("video.cam-video", { playsinline: "", muted: true, autoplay: true });
    video.setAttribute("playsinline", ""); video.muted = true;
    const status = h("div.cam-status", _("Point the camera at a barcode"));
    const torchBtn = h("button.cam-btn", { hidden: true, onclick: toggleTorch }, "\u{1F526}");
    const manual = h("div.cam-manual");
    const overlay = h("div.cam-overlay", { role: "dialog", "aria-label": _("Scan with camera") },
      video,
      h("div.cam-frame"),
      h("div.cam-top", h("div.cam-hint", hint || ""), torchBtn, h("button.cam-btn", { onclick: () => finish(null), "aria-label": _("Close") }, "✕")),
      h("div.cam-bottom", status, manual));
    document.body.appendChild(overlay);

    function finish(value) {
      if (stopped) return;
      stopped = true;
      cancelAnimationFrame(raf);
      clearTimeout(zxTimer);
      if (stream) stream.getTracks().forEach((t) => t.stop());
      overlay.remove();
      resolve(value);
    }

    function accept(text) {
      if (!normalizeScan(text)) return;
      feedback.ok();
      finish(text); // unnormalized: the field normalizes it, and a GS1 DataMatrix keeps its separators for core/gs1.js
    }

    function showFailure(message) {
      status.textContent = message;
      status.classList.add("err");
      manual.replaceChildren(h("button.btn.btn-secondary", { onclick: () => finish(null) }, _("Close")));
    }

    async function toggleTorch() {
      const track = stream && stream.getVideoTracks()[0];
      if (!track) return;
      try { torchOn = !torchOn; await track.applyConstraints({ advanced: [{ torch: torchOn }] }); } catch (e) { torchOn = false; }
    }

    async function start() {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
      } catch (e) { showFailure(cameraError(e)); return; }
      if (stopped) { stream.getTracks().forEach((t) => t.stop()); return; }
      video.srcObject = stream;
      try { await video.play(); } catch (e) { /* autoplay policy - user tapped, so this is rare */ }
      const track = stream.getVideoTracks()[0];
      try { if (track.getCapabilities && track.getCapabilities().torch) torchBtn.hidden = false; } catch (e) { /* no torch */ }

      if ("BarcodeDetector" in window) {
        let detector;
        try {
          const supported = await window.BarcodeDetector.getSupportedFormats();
          detector = new window.BarcodeDetector({ formats: FORMATS.filter((f) => supported.includes(f)) });
        } catch (e) { detector = null; }
        if (detector) {
          const tick = async () => {
            if (stopped) return;
            try {
              if (video.readyState >= 2) {
                const found = await detector.detect(video);
                if (found.length) { accept(found[0].rawValue); return; }
              }
            } catch (e) { /* frame not ready */ }
            raf = requestAnimationFrame(tick);
          };
          tick();
          return;
        }
      }
      try {
        const ZX = await loadZXing();
        const hints = new Map();
        hints.set(ZX.DecodeHintType.TRY_HARDER, true);
        hints.set(ZX.DecodeHintType.POSSIBLE_FORMATS, [
          ZX.BarcodeFormat.CODE_128, ZX.BarcodeFormat.CODE_39, ZX.BarcodeFormat.CODE_93, ZX.BarcodeFormat.EAN_13, ZX.BarcodeFormat.EAN_8,
          ZX.BarcodeFormat.UPC_A, ZX.BarcodeFormat.UPC_E, ZX.BarcodeFormat.ITF, ZX.BarcodeFormat.CODABAR, ZX.BarcodeFormat.QR_CODE, ZX.BarcodeFormat.DATA_MATRIX,
        ]);
        const reader = new ZX.MultiFormatReader();
        reader.setHints(hints);
        // Our own capture loop rather than the library's video plumbing: the stream is already playing, and grabbing a frame
        // to a canvas behaves the same on every Safari/Chrome version.
        const canvas = document.createElement("canvas");
        const g = canvas.getContext("2d", { willReadFrequently: true });
        const loop = () => {
          if (stopped) return;
          if (video.readyState >= 2 && video.videoWidth) {
            const scale = Math.min(1, 1024 / video.videoWidth);
            canvas.width = Math.round(video.videoWidth * scale); canvas.height = Math.round(video.videoHeight * scale);
            g.drawImage(video, 0, 0, canvas.width, canvas.height);
            try {
              const res = reader.decode(new ZX.BinaryBitmap(new ZX.HybridBinarizer(new ZX.HTMLCanvasElementLuminanceSource(canvas))));
              accept(res.getText());
              return;
            } catch (e) { /* no barcode in this frame */ }
          }
          zxTimer = setTimeout(loop, 140);
        };
        loop();
      } catch (e) { showFailure(e.message || _("The camera scanner could not be started.")); }
    }
    start();
  });
}
