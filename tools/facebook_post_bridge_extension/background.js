chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    try {
      if (message?.type === "bridge-ocr-image") {
        const clip = message.clip || {};
        const dataUrl = await chrome.tabs.captureVisibleTab(undefined, { format: "png" });
        const response = await fetch(dataUrl);
        const blob = await response.blob();
        const bitmap = await createImageBitmap(blob);
        const scale = Number(clip.devicePixelRatio || 1) > 0 ? Number(clip.devicePixelRatio || 1) : 1;
        const margin = Math.max(24, Math.floor(18 * scale));
        const rawX = Math.floor(Number(clip.left || 0) * scale);
        const rawY = Math.floor(Number(clip.top || 0) * scale);
        const rawW = Math.max(1, Math.floor(Number(clip.width || 1) * scale));
        const rawH = Math.max(1, Math.floor(Number(clip.height || 1) * scale));
        const sx = Math.max(0, rawX - margin);
        const sy = Math.max(0, rawY - margin);
        const sw = Math.min(bitmap.width - sx, rawW + margin * 2);
        const sh = Math.min(bitmap.height - sy, rawH + margin * 2);
        const upscale = 2;
        const canvas = new OffscreenCanvas(Math.max(1, sw * upscale), Math.max(1, sh * upscale));
        const ctx = canvas.getContext("2d");
        if (!ctx) {
          sendResponse({ ok: false, error: "ocr_canvas_unavailable" });
          return;
        }
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(bitmap, sx, sy, sw, sh, 0, 0, sw * upscale, sh * upscale);
        const croppedBlob = await canvas.convertToBlob({ type: "image/png" });
        const arrayBuffer = await croppedBlob.arrayBuffer();
        const base64 = btoa(String.fromCharCode(...new Uint8Array(arrayBuffer)));
        const ocrResponse = await fetch("http://127.0.0.1:8899/api/host-browser/ocr-extract", {
          method: "POST",
          headers: { "content-type": "application/json; charset=utf-8" },
          body: JSON.stringify({
            image_base64: base64,
            mime_type: "image/png",
            query: String(message.query || ""),
            url: String(message.url || ""),
            title: "",
            excerpt: String(message.excerpt || ""),
          }),
        });
        const data = await ocrResponse.json().catch(() => ({}));
        sendResponse({
          ok: Boolean(ocrResponse.ok && data.text),
          data,
        });
        return;
      }
      if (message?.type === "bridge-copy-text") {
        await navigator.clipboard.writeText(String(message.text || ""));
        sendResponse({ ok: true });
        return;
      }
      if (message?.type === "bridge-post-local") {
        const response = await fetch("http://127.0.0.1:3342/facebook-extension-extract", {
          method: "POST",
          headers: { "content-type": "application/json; charset=utf-8" },
          body: JSON.stringify(message.payload || {}),
        });
        const data = await response.json().catch(() => ({}));
        sendResponse({ ok: response.ok, data });
        return;
      }
      if (message?.type === "bridge-open-url") {
        const url = String(message.url || "").trim();
        if (!url) {
          sendResponse({ ok: false, error: "missing url" });
          return;
        }
        await chrome.tabs.create({ url, active: true });
        sendResponse({ ok: true });
        return;
      }
      sendResponse({ ok: false, error: "unsupported message" });
    } catch (error) {
      sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) });
    }
  })();
  return true;
});
