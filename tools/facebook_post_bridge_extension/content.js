(function () {
  const bridgeState = {
    cache: new Map(),
    observer: null,
    refreshTimer: null,
    booted: false,
  };

  const AUTO_RESOLVE_LIMIT = 6;

  function normalize(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function normalizeLoose(value) {
    return normalize(value).toLowerCase();
  }

  function stripCollapsedMarkers(value) {
    return String(value || "")
      .replace(/\b(see more|xem thêm|xem them)\b/gi, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function stripNoisePrefixes(value) {
    return String(value || "")
      .replace(/^[A-Za-z0-9._%+-]{2,}\.com[A-Za-z0-9_-]{2,}\s*/i, "")
      .replace(/^[A-Za-z0-9_-]{6,}\.com\s*/i, "")
      .replace(/^giang[#:\s-]*/i, "")
      .trim();
  }

  function stripMarkdownLinks(value) {
    return String(value || "")
      .replace(/\[\[(https?:\/\/[^\]]+)\]\((https?:\/\/[^)]+)\)\]/gi, "$1")
      .replace(/\[(https?:\/\/[^\]]+)\]\((https?:\/\/[^)]+)\)/gi, "$1")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/gi, "$1 $2")
      .trim();
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function uniqueStrings(values) {
    const out = [];
    const seen = new Set();
    for (const value of values || []) {
      const cleaned = normalize(value);
      const key = cleaned.toLowerCase();
      if (!cleaned || seen.has(key)) continue;
      seen.add(key);
      out.push(cleaned);
    }
    return out;
  }

  function looksLikeChromeNoiseText(text) {
    const sample = normalize(String(text || "")).slice(0, 240);
    if (!sample) return false;
    if (/^(facebook\s+){4,}/i.test(sample)) return true;
    if (/all reactions|comment as /i.test(sample)) return true;
    return false;
  }

  function makeCandidateKey(parts) {
    return normalize(parts.filter(Boolean).join(" | ")).toLowerCase().slice(0, 400);
  }

  function canonicalizeFacebookLink(raw) {
    const href = String(raw || "").trim();
    if (!href) return "";
    try {
      const url = new URL(href, location.href);
      const host = String(url.hostname || "").toLowerCase();
      const path = String(url.pathname || "").replace(/\/+$/, "");
      if (!/facebook\.com$/.test(host)) {
        return `${host}${path}`.toLowerCase();
      }
      if (/\/groups\/[^/]+\/permalink\/\d+/i.test(path)) return path.toLowerCase();
      if (/\/groups\/[^/]+$/i.test(path)) return path.toLowerCase();
      if (/\/posts\/\d+/i.test(path)) return path.toLowerCase();
      if (/\/photo\//i.test(path) || url.searchParams.get("fbid")) {
        const fbid = url.searchParams.get("fbid") || "";
        return fbid ? `photo:${fbid}` : path.toLowerCase();
      }
      return path.toLowerCase();
    } catch {
      return href.replace(/[?#].*$/, "").replace(/\/+$/, "").toLowerCase();
    }
  }

  function buildIdentityKey(base) {
    const textKey = normalize(String(base?.text || base?.excerpt || "")).toLowerCase().slice(0, 180);
    const linkKey = canonicalizeFacebookLink(base?.url || base?.photoOnlyUrl || "");
    const anchorKey = Array.isArray(base?.anchors)
      ? (base.anchors.map((item) => canonicalizeFacebookLink(item?.href || "")).find(Boolean) || "")
      : "";
    return makeCandidateKey([textKey, linkKey || anchorKey]);
  }

  function linkQualityScore(item) {
    const link = canonicalizeFacebookLink(item?.url || item?.photoOnlyUrl || "");
    if (/\/groups\/[^/]+\/permalink\/\d+/i.test(link) || /\/posts\/\d+/i.test(link)) return 4;
    if (/^photo:/i.test(link)) return 3;
    if (/\/groups\/[^/]+$/i.test(link)) return 2;
    if (link) return 1;
    return 0;
  }

  function extractQueryProfile(rawQuery) {
    const q = normalizeLoose(rawQuery);
    const profile = {
      query: normalize(rawQuery),
      role: [],
      seniority: [],
      location: [],
      positive: [],
      negative: [],
    };
    if (/\bdevops\b|\bsre\b|cloud|platform|infra|sysadmin/.test(q)) {
      profile.role = ["devops", "sre", "cloud", "platform", "infra", "sysadmin", "kubernetes", "docker", "terraform"];
    }
    if (/intern|thuc tap|thực tập|fresher|junior|entry level|khong can kinh nghiem|không cần kinh nghiệm/.test(q)) {
      profile.seniority = ["intern", "thực tập", "thực tập sinh", "fresher", "junior", "entry level", "không cần kinh nghiệm"];
    }
    if (/hcm|tphcm|tp hcm|ho chi minh|hồ chí minh|sai gon|sài gòn|hcmc/.test(q)) {
      profile.location = ["hcm", "tphcm", "tp hcm", "ho chi minh", "hồ chí minh", "sai gon", "sài gòn", "hcmc"];
    }
    profile.positive = uniqueStrings([
      ...profile.role,
      ...profile.seniority,
      ...profile.location,
      "tuyển",
      "tuyển dụng",
      "hiring",
      "apply",
      "ứng tuyển",
      "jd",
      "job description",
    ]);
    profile.negative = ["senior", "lead", "manager", "director", "looking for job", "tìm việc"];
    return profile;
  }

  function collectLargeImageBounds(root) {
    let best = null;
    let bestArea = 0;
    const candidates = [...root.querySelectorAll("img, [role='img']")];
    for (const node of candidates) {
      const rect = node.getBoundingClientRect?.();
      if (!rect) continue;
      if (rect.width < 120 || rect.height < 120) continue;
      const area = rect.width * rect.height;
      if (area <= bestArea) continue;
      bestArea = area;
      best = {
        x: Math.max(0, rect.left + window.scrollX),
        y: Math.max(0, rect.top + window.scrollY),
        width: Math.max(0, rect.width),
        height: Math.max(0, rect.height),
      };
    }
    return best;
  }

  function isCommentButton(el) {
    const text = normalize(el.textContent || el.innerText || "");
    const aria = normalize(el.getAttribute("aria-label") || "");
    return /(^comment$|^bình luận$|^binh luan$|leave a comment|write a comment|comment on)/i.test(text)
      || /(^comment$|^bình luận$|^binh luan$|leave a comment|write a comment|comment on)/i.test(aria);
  }

  function findCommentButton(root, story = null) {
    const searchRoot = story ? document : root;
    const nodes = [...searchRoot.querySelectorAll("div[role='button'], span[role='button'], a[role='link'], [aria-label], button")];
    const commentNodes = nodes.filter((el) => isVisibleElement(el) && isCommentButton(el));
    if (!commentNodes.length) return null;
    if (!story?.getBoundingClientRect) return commentNodes[0] || null;
    const storyRect = story.getBoundingClientRect();
    const ranked = commentNodes
      .map((el) => {
        const rect = el.getBoundingClientRect?.();
        if (!rect) return { el, score: -Infinity };
        let score = 0;
        const verticalGap = Math.abs(rect.top - storyRect.bottom);
        score -= verticalGap;
        if (rect.top >= storyRect.top) score += 120;
        if (rect.top >= storyRect.bottom - 120 && rect.top <= storyRect.bottom + 260) score += 180;
        if (Math.abs(rect.left - storyRect.left) < Math.max(220, storyRect.width * 0.5)) score += 40;
        if (Math.abs(rect.left - storyRect.left) > Math.max(480, storyRect.width * 1.2)) score -= 500;
        if (rect.top < storyRect.top - 80 || rect.top > storyRect.bottom + 420) score -= 500;
        return { el, score };
      })
      .sort((a, b) => b.score - a.score);
    return ranked[0]?.el || null;
  }

  function collectPostAnchors(root) {
    return [...root.querySelectorAll("a[href]")].map((anchor) => ({
      href: String(anchor.href || ""),
      text: normalize(anchor.textContent || anchor.innerText || anchor.getAttribute("aria-label") || ""),
    }));
  }

  function pickBestCandidateText(primaryText, anchors) {
    const cleanText = (value) => stripCollapsedMarkers(stripMarkdownLinks(stripNoisePrefixes(normalize(value || ""))));
    const baseText = cleanText(primaryText || "");
    const anchorTexts = (Array.isArray(anchors) ? anchors : [])
      .map((item) => cleanText(item?.text || ""))
      .filter((text) => text.length >= 80)
      .filter((text) => !/^s?rs?poo/i.test(text))
      .filter((text) => !/^[a-z0-9]{10,}$/i.test(text))
      .filter((text) => !/^https?:\/\//i.test(text))
      .filter((text) => !/^(like|comment|share|join|see more|xem thêm)$/i.test(text))
      .sort((a, b) => b.length - a.length);
    const anchorBest = anchorTexts[0] || "";
    if (!anchorBest) return baseText;
    if (!baseText) return anchorBest;
    if (anchorBest.length >= baseText.length + 40) return anchorBest;
    return baseText;
  }

  function buildExcerpt(text) {
    return stripCollapsedMarkers(stripNoisePrefixes(stripMarkdownLinks(normalize(text || "")))).slice(0, 240);
  }

  function isVisibleElement(el) {
    const rect = el?.getBoundingClientRect?.();
    if (!rect) return false;
    if (rect.width <= 0 || rect.height <= 0) return false;
    if (rect.bottom < 0 || rect.top > window.innerHeight) return false;
    return true;
  }

  function findSeeMoreButtons(root) {
    const nodes = [...root.querySelectorAll("div[role='button'], span[role='button'], a[role='link'], button")];
    return nodes.filter((el) => {
      if (!isVisibleElement(el)) return false;
      const text = normalize(el.textContent || el.innerText || "");
      const aria = normalize(el.getAttribute("aria-label") || "");
      if (!text && !aria) return false;
      if (/^(see more|xem thêm|xem them|more)$/i.test(text) || /^(see more|xem thêm|xem them)$/i.test(aria)) {
        return true;
      }
      if (/see more|xem thêm|xem them/i.test(text) || /see more|xem thêm|xem them/i.test(aria)) {
        return true;
      }
      return false;
    });
  }

  function dispatchHumanClick(el) {
    if (!el) return false;
    try {
      el.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, cancelable: true, pointerType: "mouse", isPrimary: true, button: 0, buttons: 1 }));
      el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, button: 0, buttons: 1 }));
      el.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, cancelable: true, pointerType: "mouse", isPrimary: true, button: 0, buttons: 0 }));
      el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, button: 0, buttons: 0 }));
      el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, button: 0, buttons: 0 }));
      if (typeof el.click === "function") el.click();
      return true;
    } catch {
      try {
        if (typeof el.click === "function") {
          el.click();
          return true;
        }
      } catch {}
    }
    return false;
  }

  function maybeHasCollapsedText(root) {
    const text = normalize(root?.innerText || "");
    return /\b(see more|xem thêm|xem them)\b/i.test(text);
  }

  function collectSeeMoreSearchRoots(root) {
    const roots = [];
    let current = root;
    let hops = 0;
    while (current && hops < 4) {
      roots.push(current);
      current = current.parentElement;
      hops += 1;
    }
    return roots;
  }

  async function waitForExpansion(root, baselineLength, timeoutMs = 1200) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      const nextLength = stripCollapsedMarkers(normalize(root?.innerText || "")).length;
      if (nextLength > baselineLength + 24 || !maybeHasCollapsedText(root)) {
        return true;
      }
      await sleep(120);
    }
    return false;
  }

  async function expandVisibleSeeMore(root) {
    if (!maybeHasCollapsedText(root)) return 0;
    const beforeLength = stripCollapsedMarkers(normalize(root?.innerText || "")).length;
    const buttonMap = new Map();
    for (const scope of collectSeeMoreSearchRoots(root)) {
      for (const button of findSeeMoreButtons(scope)) {
        const key = normalize(button.textContent || button.innerText || button.getAttribute("aria-label") || "");
        if (!buttonMap.has(key)) buttonMap.set(key, button);
      }
    }
    const buttons = [...buttonMap.values()].slice(0, 8);
    let clicked = 0;
    for (const button of buttons) {
      try {
        button.scrollIntoView({ behavior: "instant", block: "center", inline: "nearest" });
        await sleep(60);
        dispatchHumanClick(button);
        clicked += 1;
        const expanded = await waitForExpansion(root, beforeLength, 1400);
        if (expanded) break;
      } catch {}
    }
    if (clicked && maybeHasCollapsedText(root)) {
      const secondPass = [...buttonMap.values()].slice(0, 6);
      const secondBaseline = stripCollapsedMarkers(normalize(root?.innerText || "")).length;
      for (const button of secondPass) {
        try {
          button.scrollIntoView({ behavior: "instant", block: "center", inline: "nearest" });
          await sleep(40);
          dispatchHumanClick(button);
          clicked += 1;
          const expanded = await waitForExpansion(root, secondBaseline, 1200);
          if (expanded) break;
        } catch {}
      }
    }
    return clicked;
  }

  function findCandidateContainers() {
    const storyNodes = [
      ...document.querySelectorAll("[data-ad-rendering-role='story_message'], div[data-ad-preview='message'], div[data-ad-comet-preview='message']")
    ];
    const seen = new Set();
    const containers = [];
    for (const node of storyNodes) {
      let current = node;
      let best = node;
      let hops = 0;
      while (current && hops < 8) {
        const text = normalize(current.innerText || "");
        const links = current.querySelectorAll ? current.querySelectorAll("a[href]").length : 0;
        if (text.length >= 80 && text.length <= 7000) best = current;
        if (links >= 3) {
          best = current;
          break;
        }
        current = current.parentElement;
        hops += 1;
      }
      const key = normalize(best.innerText || "").slice(0, 180).toLowerCase();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      containers.push({ root: best, story: node });
    }
    return containers;
  }

  function classifyLink(href) {
    const url = String(href || "");
    if (!/facebook\.com/i.test(url)) return "external";
    if (/\/permalink\//i.test(url) || /story_fbid=/i.test(url) || /\/posts\//i.test(url) || /permalink\.php/i.test(url)) return "permalink";
    if (/\/groups\/[^/]+\/?(?:\?|$)/i.test(url) || /\/groups\/[^/]+\/user\//i.test(url)) return "group";
    if (/\/photo\/\?/i.test(url) || /[?&]fbid=/i.test(url)) return "photo";
    if (/\/search\/top/i.test(url)) return "search";
    return "other_facebook";
  }

  function extractBestLink(root) {
    const anchors = [...root.querySelectorAll("a[href]")].map((a) => {
      const href = String(a.href || "");
      const text = normalize(a.textContent || a.innerText || a.getAttribute("aria-label") || "");
      const kind = classifyLink(href);
      return { href, text, kind };
    });
    const photoLinks = uniqueStrings(anchors.filter((item) => item.kind === "photo").map((item) => item.href));
    const ranked = anchors
      .map((item) => {
        let score = 0;
        if (item.kind === "permalink") score += 30;
        else if (item.kind === "group") score += 18;
        else if (item.kind === "other_facebook") score += 8;
        else if (item.kind === "photo") score += 3;
        else if (item.kind === "search") score -= 10;
        if (/comment|share|like|join/i.test(item.text)) score -= 5;
        return { ...item, score };
      })
      .filter((item) => item.kind !== "search")
      .sort((a, b) => b.score - a.score);
    const bestUsable = ranked.find((item) => item.kind === "permalink" || item.kind === "group" || item.kind === "other_facebook") || null;
    const photoOnlyUrl = !bestUsable && photoLinks.length === 1 ? photoLinks[0] : "";
    return {
      url: bestUsable?.href || "",
      linkKind: bestUsable?.kind || (photoOnlyUrl ? "photo_only" : ""),
      photoOnlyUrl,
      photoLinkCount: photoLinks.length,
    };
  }

  function extractCandidateBase(root, story) {
    const storyText = stripCollapsedMarkers(normalize(story?.innerText || ""));
    const rootText = stripCollapsedMarkers(normalize(root?.innerText || ""));
    const rawText = (!looksLikeChromeNoiseText(rootText) && rootText.length >= storyText.length + 20)
      ? rootText
      : storyText;
    const anchors = collectPostAnchors(root).slice(0, 12);
    const text = pickBestCandidateText(rawText, anchors);
    const excerpt = buildExcerpt(text);
    const linkInfo = extractBestLink(root);
    const commentButton = findCommentButton(root, story);
    const rect = root.getBoundingClientRect();
    const imageBounds = collectLargeImageBounds(root);
    const base = {
      cacheKey: "",
      text,
      excerpt,
      url: linkInfo.url,
      linkKind: linkInfo.linkKind,
      photoOnlyUrl: linkInfo.photoOnlyUrl,
      photoLinkCount: linkInfo.photoLinkCount,
      ocrCandidate: Boolean(linkInfo.photoOnlyUrl && linkInfo.photoLinkCount === 1),
      truncatedHint: maybeHasCollapsedText(root),
      hasComment: Boolean(commentButton),
      hasLargeImage: Boolean(imageBounds),
      imageBounds,
      bounds: {
        x: Math.max(0, rect.left + window.scrollX),
        y: Math.max(0, rect.top + window.scrollY),
        width: Math.max(0, rect.width),
        height: Math.max(0, rect.height),
      },
      anchors,
      observedAt: new Date().toISOString(),
    };
    base.cacheKey = buildIdentityKey(base);
    return base;
  }

  function scoreCandidate(base, profile, index) {
    const text = normalize(base.text || "");
    const excerpt = normalize(base.excerpt || "");
    const hay = normalizeLoose(text);
    const roleHit = profile.role.some((term) => hay.includes(normalizeLoose(term)));
    const seniorityHit = profile.seniority.some((term) => hay.includes(normalizeLoose(term)));
    const locationHit = !profile.location.length || profile.location.some((term) => hay.includes(normalizeLoose(term)));
    const positiveHits = profile.positive.filter((term) => hay.includes(normalizeLoose(term)));
    const negativeHits = profile.negative.filter((term) => hay.includes(normalizeLoose(term)));
    let score = 0;
    if (roleHit) score += 6;
    if (seniorityHit) score += 4;
    if (locationHit) score += 2;
    score += Math.min(4, positiveHits.length);
    score -= Math.min(6, negativeHits.length * 3);
    if (base.hasLargeImage) score += 1.5;
    if (base.ocrCandidate) score += 1;
    return {
      ...base,
      index,
      text,
      excerpt,
      score,
      matches: {
        roleHit,
        seniorityHit,
        locationHit,
        positiveHits,
        negativeHits,
      },
    };
  }

  function updateCacheFromContainers(containers) {
    for (const { root, story } of containers) {
      const base = extractCandidateBase(root, story);
      if (!base.cacheKey) continue;
      const existing = bridgeState.cache.get(base.cacheKey);
      bridgeState.cache.set(base.cacheKey, {
        ...existing,
        ...base,
      });
    }
  }

  function scheduleCacheRefresh() {
    if (bridgeState.refreshTimer) clearTimeout(bridgeState.refreshTimer);
    bridgeState.refreshTimer = setTimeout(() => {
      bridgeState.refreshTimer = null;
      try {
        updateCacheFromContainers(findCandidateContainers());
      } catch {}
    }, 180);
  }

  function ensureBridgeRuntime() {
    if (bridgeState.booted) return;
    bridgeState.booted = true;
    window.addEventListener("scroll", scheduleCacheRefresh, { passive: true });
    bridgeState.observer = new MutationObserver(() => scheduleCacheRefresh());
    bridgeState.observer.observe(document.body, {
      childList: true,
      subtree: true,
    });
    scheduleCacheRefresh();
  }

  function readScrollProgressSignal() {
    const visibleCount = findCandidateContainers().length;
    const cacheCount = bridgeState.cache.size;
    const scrollHeight = Math.max(
      Number(document.body?.scrollHeight || 0),
      Number(document.documentElement?.scrollHeight || 0),
    );
    return { visibleCount, cacheCount, scrollHeight };
  }

  async function waitForAdditionalScrollContent(previous, timeoutMs = 1800) {
    const startedAt = Date.now();
    while ((Date.now() - startedAt) < timeoutMs) {
      await sleep(220);
      scheduleCacheRefresh();
      const current = readScrollProgressSignal();
      if (
        current.visibleCount > Number(previous?.visibleCount || 0)
        || current.cacheCount > Number(previous?.cacheCount || 0)
        || current.scrollHeight > Number(previous?.scrollHeight || 0)
      ) {
        return current;
      }
    }
    return readScrollProgressSignal();
  }

  async function scrollBurst(rounds = 4) {
    ensureBridgeRuntime();
    const total = Math.max(1, Math.min(Number(rounds || 4), 8));
    for (let i = 0; i < total; i += 1) {
      const before = readScrollProgressSignal();
      window.scrollBy({
        top: Math.max(window.innerHeight * 1.1, 1000),
        left: 0,
        behavior: "instant",
      });
      scheduleCacheRefresh();
      await waitForAdditionalScrollContent(before, 1800);
    }
    await sleep(450);
  }

  async function extractVisibleCandidates(rawQuery) {
    ensureBridgeRuntime();
    const containers = findCandidateContainers();
    for (const { root, story } of containers) {
      const base = extractCandidateBase(root, story);
      if (base.hasLargeImage) continue;
      if (!base.truncatedHint) continue;
      await expandVisibleSeeMore(root);
    }
    updateCacheFromContainers(findCandidateContainers());
    const profile = extractQueryProfile(rawQuery);
    const scored = [...bridgeState.cache.values()]
      .map((item, index) => scoreCandidate(item, profile, index))
      .filter((item) => item.text || item.url || item.ocrCandidate)
      .sort((a, b) => b.score - a.score);
    const items = [];
    const seen = new Set();
    for (const item of scored) {
      const textKey = normalize(String(item.text || item.excerpt || "")).toLowerCase().slice(0, 180);
      const dedupeKey = makeCandidateKey([textKey, canonicalizeFacebookLink(item.url || item.photoOnlyUrl || "")]);
      if (dedupeKey && seen.has(dedupeKey)) continue;
      if (dedupeKey) seen.add(dedupeKey);
      items.push(item);
    }
    return {
      ok: true,
      query: profile.query,
      profile,
      title: document.title,
      url: location.href,
      cached_count: bridgeState.cache.size,
      visible_count: containers.length,
      count: items.length,
      items: items.slice(0, 50),
    };
  }

  function findBestPermalinkFromScope(scope) {
    const anchors = [...scope.querySelectorAll("a[href]")];
    const ranked = anchors
      .map((a) => {
        const href = String(a.href || "");
        let score = 0;
        if (/\/permalink\//i.test(href) || /story_fbid=/i.test(href) || /\/posts\//i.test(href) || /permalink\.php/i.test(href)) score += 20;
        if (/facebook\.com\/groups\//i.test(href)) score += 5;
        if (/\/photo\/\?/i.test(href) || /[?&]fbid=/i.test(href)) score += 8;
        return { href, score };
      })
      .filter((item) => /facebook\.com/i.test(item.href))
      .sort((a, b) => b.score - a.score);
    return ranked[0]?.href || "";
  }

  function getOverlayRoot() {
    const dialogs = [...document.querySelectorAll("[role='dialog']")];
    if (!dialogs.length) return null;
    return dialogs
      .map((el) => ({ el, len: normalize(el.innerText || "").length }))
      .sort((a, b) => b.len - a.len)[0]?.el || null;
  }

  function cleanOverlayBodyText(raw) {
    const lines = String(raw || "")
      .split(/\n+/)
      .map((line) => normalize(line))
      .filter(Boolean);
    const kept = [];
    for (const line of lines) {
      const low = line.toLowerCase();
      if (/^(like|comment|share|reply|send|edited|most relevant)$/i.test(line)) continue;
      if (/^comment as /i.test(low)) continue;
      if (/^all reactions/i.test(low)) continue;
      if (/^\d+\s+(comments?|shares?)$/i.test(low)) continue;
      if (/^facebook$/i.test(low)) continue;
      if (/^(join|admin|moderator)$/i.test(low)) continue;
      if (/^[rsotepndh0-9:\s.lcuamefngjhu-]{12,}$/i.test(low)) continue;
      kept.push(line);
    }
    const startIndex = kept.findIndex((line) => {
      const low = line.toLowerCase();
      if (line.length < 40) return false;
      if (/^(facebook|join|admin|moderator)$/i.test(low)) return false;
      if (/^[rsotepndh0-9:\s.lcuamefngjhu-]{12,}$/i.test(low)) return false;
      return true;
    });
    const trimmed = startIndex >= 0 ? kept.slice(startIndex) : kept;
    return trimmed.join("\n").trim();
  }

  async function waitForOverlay(timeoutMs = 6000) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      const root = getOverlayRoot();
      if (root) {
        const text = cleanOverlayBodyText(root.innerText || "");
        if (text.length >= 80) {
          return root;
        }
      }
      await sleep(250);
    }
    return getOverlayRoot();
  }

  async function waitForUrlChange(previousUrl, timeoutMs = 5000) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      if (String(location.href || "") !== String(previousUrl || "")) {
        return String(location.href || "");
      }
      await sleep(150);
    }
    return "";
  }

  async function waitForUrlRestore(previousUrl, timeoutMs = 8000) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      if (String(location.href || "") === String(previousUrl || "")) {
        const containers = findCandidateContainers();
        if (containers.length) return true;
      }
      await sleep(200);
    }
    return String(location.href || "") === String(previousUrl || "");
  }

  function extractResolvedTextFromCurrentPage(seedText = "") {
    const seed = normalize(String(seedText || ""));
    const containers = findCandidateContainers()
      .map(({ root, story }) => extractCandidateBase(root, story))
      .filter((item) => normalize(item.text || "").length >= 40);
    const best = containers
      .map((item) => ({
        item,
        score: overlapScore(item.text || item.excerpt || "", seed),
      }))
      .sort((a, b) => b.score - a.score)[0];
    if (best?.item?.text && best.score >= 3) {
      return normalize(best.item.text || "");
    }
    return cleanOverlayBodyText(document.body?.innerText || "");
  }

  async function closeOverlay() {
    const closeButton = [...document.querySelectorAll("[role='dialog'] [aria-label], [role='dialog'] div[role='button'], [role='dialog'] button")]
      .find((el) => /close|đóng/i.test(normalize(el.getAttribute?.("aria-label") || el.textContent || "")));
    if (closeButton) {
      closeButton.click();
      await sleep(250);
      return;
    }
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    document.dispatchEvent(new KeyboardEvent("keyup", { key: "Escape", bubbles: true }));
    await sleep(250);
  }

  async function resolveCandidateByComment(rawQuery, cacheKey) {
    const payload = await extractVisibleCandidates(rawQuery);
    const item = (payload.items || []).find((entry) => String(entry.cacheKey || "") === String(cacheKey || ""));
    if (!item) {
      return { ok: false, error: "candidate not found" };
    }
    const containers = findCandidateContainers();
    const pair = containers.find(({ root, story }) => {
      const base = extractCandidateBase(root, story);
      return String(base.cacheKey || "") === String(cacheKey || "");
    });
    if (!pair?.root) {
      return { ok: false, error: "candidate root not found in current view", item };
    }
    pair.root.scrollIntoView({ behavior: "instant", block: "center", inline: "nearest" });
    await sleep(250);
    const commentButton = findCommentButton(pair.root, pair.story);
    if (!commentButton) {
      return { ok: false, error: "comment button not found", item };
    }
    const previousUrl = String(location.href || "");
    commentButton.click();
    await sleep(300);
    const changedUrl = await waitForUrlChange(previousUrl, 2500);
    if (changedUrl) {
      await sleep(1200);
      const pageText = extractResolvedTextFromCurrentPage(item.text || item.excerpt || "");
      const result = {
        index: item.index,
        cacheKey: item.cacheKey,
        query: rawQuery,
        source_url: previousUrl,
        url: changedUrl,
        link_kind: classifyLink(changedUrl),
        photo_only_url: item.photoOnlyUrl || "",
        photo_link_count: Number(item.photoLinkCount || 0),
        ocr_candidate: Boolean(item.ocrCandidate),
        excerpt: item.excerpt || "",
        raw_text: item.text || "",
        overlay_text: pageText,
        body_text: pageText.slice(0, 4000),
        has_comment: item.hasComment,
        has_large_image: item.hasLargeImage,
        matches: item.matches || {},
      };
      history.back();
      await waitForUrlRestore(previousUrl, 9000);
      return { ok: true, result };
    }
    const overlay = await waitForOverlay(7000);
    if (!overlay) {
      return { ok: false, error: "overlay not found after comment click", item };
    }
    await expandVisibleSeeMore(overlay);
    await sleep(180);
    const overlayText = cleanOverlayBodyText(overlay.innerText || "");
    const permalink = findBestPermalinkFromScope(overlay) || findBestPermalinkFromScope(document) || item.url || "";
    await closeOverlay();
    return {
      ok: true,
      result: {
        index: item.index,
        cacheKey: item.cacheKey,
        query: payload.query,
        source_url: location.href,
        url: permalink,
        link_kind: item.linkKind || "",
        photo_only_url: item.photoOnlyUrl || "",
        photo_link_count: Number(item.photoLinkCount || 0),
        ocr_candidate: Boolean(item.ocrCandidate),
        excerpt: item.excerpt || "",
        raw_text: item.text || "",
        overlay_text: overlayText,
        body_text: overlayText.slice(0, 4000),
        has_comment: item.hasComment,
        has_large_image: item.hasLargeImage,
        matches: item.matches || {},
      },
    };
  }

  async function prepareCandidateImageClip(rawQuery, cacheKey) {
    const payload = await extractVisibleCandidates(rawQuery);
    const item = (payload.items || []).find((entry) => String(entry.cacheKey || "") === String(cacheKey || ""));
    if (!item) {
      return { ok: false, error: "candidate not found" };
    }
    const containers = findCandidateContainers();
    const pair = containers.find(({ root, story }) => {
      const base = extractCandidateBase(root, story);
      return String(base.cacheKey || "") === String(cacheKey || "");
    });
    if (!pair?.root) {
      return { ok: false, error: "candidate root not found in current view", item };
    }
    pair.root.scrollIntoView({ behavior: "instant", block: "center", inline: "nearest" });
    await sleep(250);
    const refreshed = extractCandidateBase(pair.root, pair.story);
    const clip = refreshed.imageBounds || null;
    if (!clip) {
      return { ok: false, error: "image bounds not found", item: refreshed };
    }
    return {
      ok: true,
      result: {
        cacheKey: refreshed.cacheKey,
        query: payload.query,
        url: refreshed.photoOnlyUrl || refreshed.url || "",
        excerpt: refreshed.excerpt || "",
        raw_text: refreshed.text || "",
        clip: {
          left: Math.max(0, clip.x - window.scrollX),
          top: Math.max(0, clip.y - window.scrollY),
          width: Math.max(1, clip.width),
          height: Math.max(1, clip.height),
          devicePixelRatio: window.devicePixelRatio || 1,
        },
      },
    };
  }

  function shouldAutoResolveByComment(item) {
    return false;
  }

  function autoResolvePriority(item) {
    if (!item) return 0;
    if (item.ocrCandidate) return 300;
    if (!item.hasLargeImage && item.truncatedHint && item.hasComment) return 250;
    if (!item.hasLargeImage && !item.url && item.hasComment) return 220;
    if (!item.hasLargeImage && (item.linkKind === "group" || item.linkKind === "other_facebook") && item.hasComment) return 180;
    return Number(item.score || 0);
  }

  async function autoResolveCandidates(rawQuery, payload) {
    const items = Array.isArray(payload?.items)
      ? payload.items
        .slice()
        .sort((a, b) => autoResolvePriority(b) - autoResolvePriority(a))
        .slice(0, AUTO_RESOLVE_LIMIT)
      : [];
    const resolved = [];
    for (const item of items) {
      const cacheKey = String(item?.cacheKey || "");
      if (!cacheKey) continue;
      try {
        if (item.ocrCandidate) {
          const prepared = await prepareCandidateImageClip(rawQuery, cacheKey);
          if (prepared?.ok && prepared.result?.clip) {
            const ocr = await chrome.runtime.sendMessage({
              type: "bridge-ocr-image",
              clip: prepared.result.clip,
              query: prepared.result.query || rawQuery,
              url: prepared.result.url || "",
              excerpt: prepared.result.excerpt || item.excerpt || "",
            });
            if (ocr?.ok) {
              const existing = bridgeState.cache.get(cacheKey) || {};
              bridgeState.cache.set(cacheKey, {
                ...existing,
                imageText: String(ocr.data?.text || "").trim(),
                url: prepared.result.url || existing.url || "",
                linkKind: existing.linkKind || "photo_only",
              });
              resolved.push({ cacheKey, mode: "ocr", ok: true });
            } else {
              resolved.push({ cacheKey, mode: "ocr", ok: false, error: ocr?.error || ocr?.data?.reason || "ocr_failed" });
            }
          }
          continue;
        }
        if (!shouldAutoResolveByComment(item)) continue;
        const response = await Promise.race([
          resolveCandidateByComment(rawQuery, cacheKey),
          sleep(9000).then(() => ({ ok: false, error: "comment_resolve_timeout" })),
        ]);
        if (response?.ok) {
          const existing = bridgeState.cache.get(cacheKey) || {};
          bridgeState.cache.set(cacheKey, {
            ...existing,
            text: String(response.result?.body_text || response.result?.overlay_text || response.result?.raw_text || existing.text || "").trim(),
            excerpt: buildExcerpt(response.result?.body_text || response.result?.overlay_text || response.result?.raw_text || existing.excerpt || ""),
            url: String(response.result?.url || existing.url || "").trim(),
            linkKind: response.result?.url ? classifyLink(response.result.url) : (existing.linkKind || ""),
            resolvedByComment: true,
          });
          resolved.push({ cacheKey, mode: "comment", ok: true });
        } else {
          resolved.push({ cacheKey, mode: "comment", ok: false, error: response?.error || "comment_resolve_failed" });
        }
      } catch (error) {
        resolved.push({
          cacheKey,
          mode: item.ocrCandidate ? "ocr" : "comment",
          ok: false,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    }
    return resolved;
  }

  async function handlePageBridgeRequest(request = {}) {
    const type = String(request.type || "");
    if (type === "bridge-extract-visible") {
      return await extractVisibleCandidates(String(request.query || ""));
    }
    if (type === "bridge-scroll-scan") {
      await scrollBurst(Number(request.rounds || 4));
      return await extractVisibleCandidates(String(request.query || ""));
    }
    if (type === "bridge-scroll-scan-resolve") {
      await scrollBurst(Number(request.rounds || 4));
      const query = String(request.query || "");
      const scanned = await extractVisibleCandidates(query);
      const resolved = await autoResolveCandidates(query, scanned);
      const rescanned = await extractVisibleCandidates(query);
      return {
        ...rescanned,
        auto_resolved_count: resolved.filter((item) => item.ok).length,
        auto_resolve_attempts: resolved.length,
        auto_resolve_log: resolved,
      };
    }
    if (type === "bridge-resolve-candidate") {
      return await resolveCandidateByComment(String(request.query || ""), String(request.cacheKey || ""));
    }
    if (type === "bridge-prepare-ocr-candidate") {
      return await prepareCandidateImageClip(String(request.query || ""), String(request.cacheKey || ""));
    }
    return { ok: false, error: "unsupported page request" };
  }

  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data || {};
    if (!data || data.__fbBridgeExtensionRequest !== true) return;
    const nonce = String(data.nonce || "");
    Promise.resolve(handlePageBridgeRequest(data.request || {}))
      .then((payload) => {
        window.postMessage({ __fbBridgeExtensionResponse: true, nonce, payload }, "*");
      })
      .catch((error) => {
        window.postMessage(
          {
            __fbBridgeExtensionResponse: true,
            nonce,
            payload: { ok: false, error: error instanceof Error ? error.message : String(error) },
          },
          "*",
        );
      });
  });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    (async () => {
      try {
        if (typeof message?.type === "string" && message.type.startsWith("bridge-")) {
          sendResponse(await handlePageBridgeRequest(message));
          return;
        }
        sendResponse({ ok: false, error: "unsupported message" });
      } catch (error) {
        sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) });
      }
    })();
    return true;
  });
})();
