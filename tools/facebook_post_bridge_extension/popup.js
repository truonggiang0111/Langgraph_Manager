const queryInput = document.getElementById("query");
const scanButton = document.getElementById("scan");
const scrollScanButton = document.getElementById("scroll-scan");
const copyJsonButton = document.getElementById("copy-json");
const copyTextButton = document.getElementById("copy-text");
const sendLocalButton = document.getElementById("send-local");
const statusEl = document.getElementById("status");
const metaEl = document.getElementById("meta");
const resultsEl = document.getElementById("results");
const outputEl = document.getElementById("output");

let lastPayload = null;

function setStatus(text) {
  statusEl.textContent = text || "";
}

function getItemLink(item) {
  return String(item?.url || item?.photoOnlyUrl || item?.link || "").trim();
}

function buildLocalPayload(payload) {
  const items = (payload?.items || []).map((item) => ({
    text: String(item?.text || item?.excerpt || "").trim(),
    link: getItemLink(item),
    image_text: String(item?.imageText || "").trim(),
  })).filter((item) => item.text || item.link || item.image_text);
  return {
    title: payload?.title || "",
    url: payload?.url || "",
    query: payload?.query || "",
    count: items.length,
    items,
  };
}

function renderResults(payload) {
  lastPayload = payload;
  const resolvedMeta = payload.auto_resolve_attempts
    ? ` · auto=${payload.auto_resolved_count || 0}/${payload.auto_resolve_attempts}`
    : "";
  metaEl.textContent = `${payload.count || 0} candidates · cached=${payload.cached_count || 0} · visible=${payload.visible_count || 0}${resolvedMeta} · ${payload.title || ""}`;
  resultsEl.innerHTML = "";
  for (const item of payload.items || []) {
    const card = document.createElement("div");
    card.className = "card";
    const text = String(item.text || item.excerpt || "").trim();
    const link = getItemLink(item);
    const imageText = String(item.imageText || "").trim();
    card.innerHTML = `
      <div class="title">${(text || "Untitled").slice(0, 90)}</div>
      <div class="small"><strong>text</strong>: ${(text || "").slice(0, 260)}</div>
      <div class="small"><strong>link</strong>: ${(link || "").slice(0, 180)}</div>
      <div class="small"><strong>image_text</strong>: ${(imageText || "").slice(0, 180)}</div>
      <div class="actions">
        <button data-copy="${item.index}">Copy text</button>
        <button data-open="${item.index}">Open link</button>
        <button data-read="${item.index}">Read via Comment</button>
        <button data-ocr="${item.index}">OCR Photo</button>
      </div>
    `;
    resultsEl.appendChild(card);
  }
  resultsEl.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const item = lastPayload?.items?.[Number(button.dataset.copy)];
      if (!item) return;
      await chrome.runtime.sendMessage({ type: "bridge-copy-text", text: item.text || item.excerpt || "" });
      setStatus("Copied candidate text.");
    });
  });
  resultsEl.querySelectorAll("[data-open]").forEach((button) => {
    button.addEventListener("click", async () => {
      const item = lastPayload?.items?.[Number(button.dataset.open)];
      const link = getItemLink(item);
      if (!link) return;
      await chrome.runtime.sendMessage({ type: "bridge-open-url", url: link });
      setStatus("Opened candidate link.");
    });
  });
  resultsEl.querySelectorAll("[data-read]").forEach((button) => {
    button.addEventListener("click", async () => {
      const item = lastPayload?.items?.[Number(button.dataset.read)];
      if (!item) return;
      try {
        setStatus("Resolving comment overlay...");
        const response = await resolveCandidateByComment(item.cacheKey);
        const result = response?.result || {};
        item.text = String(result.body_text || result.overlay_text || result.raw_text || item.text || "").trim();
        item.link = String(result.url || getItemLink(item) || "").trim();
        outputEl.value = [
          `ok=${Boolean(response?.ok)}`,
          `url=${item.link || ""}`,
          "",
          item.text || "",
        ].join("\n");
        setStatus(response?.ok ? "Resolved comment overlay." : (response?.error || "Resolve failed"));
      } catch (error) {
        outputEl.value = "";
        setStatus(error instanceof Error ? error.message : String(error));
      }
    });
  });
  resultsEl.querySelectorAll("[data-ocr]").forEach((button) => {
    button.addEventListener("click", async () => {
      const item = lastPayload?.items?.[Number(button.dataset.ocr)];
      if (!item) return;
      try {
        setStatus("Preparing OCR capture...");
        const tab = await getActiveFacebookTab();
        const prepared = await chrome.tabs.sendMessage(tab.id, {
          type: "bridge-prepare-ocr-candidate",
          query: queryInput.value.trim(),
          cacheKey: item.cacheKey,
        });
        if (!prepared?.ok) throw new Error(prepared?.error || "Prepare OCR failed");
        setStatus("Running OCR...");
        const ocr = await chrome.runtime.sendMessage({
          type: "bridge-ocr-image",
          clip: prepared.result?.clip || {},
          query: prepared.result?.query || queryInput.value.trim(),
          url: prepared.result?.url || "",
          excerpt: prepared.result?.excerpt || item.excerpt || "",
        });
        if (!ocr?.ok) throw new Error(ocr?.data?.reason || ocr?.error || "OCR failed");
        item.imageText = String(ocr.data?.text || "").trim();
        item.link = prepared.result?.url || getItemLink(item);
        outputEl.value = [
          `ok=true`,
          `url=${prepared.result?.url || ""}`,
          `confidence=${ocr.data?.confidence || ""}`,
          "",
          item.imageText || "",
        ].join("\n");
        setStatus("OCR complete.");
      } catch (error) {
        setStatus(error instanceof Error ? error.message : String(error));
      }
    });
  });
}

async function getActiveFacebookTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error("No active tab");
  return tab;
}

async function scanCurrentTab() {
  const tab = await getActiveFacebookTab();
  const query = queryInput.value.trim();
  const response = await chrome.tabs.sendMessage(tab.id, {
    type: "bridge-extract-visible",
    query,
  });
  if (!response?.ok) {
    throw new Error(response?.error || "Scan failed");
  }
  renderResults(response);
  setStatus("Scan complete.");
}

async function scrollAndScanCurrentTab() {
  const tab = await getActiveFacebookTab();
  const query = queryInput.value.trim();
  const response = await chrome.tabs.sendMessage(tab.id, {
    type: "bridge-scroll-scan-resolve",
    query,
    rounds: 4,
  });
  if (!response?.ok) {
    throw new Error(response?.error || "Scroll + scan failed");
  }
  renderResults(response);
  setStatus("Scroll + scan complete.");
}

async function resolveCandidateByComment(cacheKey) {
  const tab = await getActiveFacebookTab();
  const query = queryInput.value.trim();
  const response = await chrome.tabs.sendMessage(tab.id, {
    type: "bridge-resolve-candidate",
    query,
    cacheKey,
  });
  if (!response?.ok) {
    throw new Error(response?.error || "Resolve failed");
  }
  return response;
}

scanButton.addEventListener("click", async () => {
  try {
    setStatus("Scanning visible Facebook posts...");
    await scanCurrentTab();
  } catch (error) {
    setStatus(error instanceof Error ? error.message : String(error));
  }
});

scrollScanButton.addEventListener("click", async () => {
  try {
    setStatus("Scrolling, resolving, and accumulating candidates...");
    await scrollAndScanCurrentTab();
    setStatus("Scroll + scan + resolve complete.");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : String(error));
  }
});

copyJsonButton.addEventListener("click", async () => {
  if (!lastPayload) {
    setStatus("No payload yet.");
    return;
  }
  await chrome.runtime.sendMessage({ type: "bridge-copy-text", text: JSON.stringify(buildLocalPayload(lastPayload), null, 2) });
  setStatus("Copied JSON.");
});

copyTextButton.addEventListener("click", async () => {
  const item = lastPayload?.items?.[0];
  if (!item) {
    setStatus("No top candidate.");
    return;
  }
  await chrome.runtime.sendMessage({ type: "bridge-copy-text", text: item.text || item.excerpt || "" });
  setStatus("Copied top candidate text.");
});

sendLocalButton.addEventListener("click", async () => {
  if (!lastPayload) {
    setStatus("No payload yet.");
    return;
  }
  const response = await chrome.runtime.sendMessage({
    type: "bridge-post-local",
    payload: buildLocalPayload(lastPayload),
  });
  setStatus(response?.ok ? "Sent to local worker." : (response?.error || "Send failed"));
});
