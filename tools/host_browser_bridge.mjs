import http from "node:http";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";

const port = Number(process.env.HOST_BROWSER_BRIDGE_PORT || 3342);
const host = process.env.HOST_BROWSER_BRIDGE_HOST || "0.0.0.0";
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const artifactDir = process.env.HOST_BROWSER_ARTIFACT_DIR || path.join(__dirname, "..", "data", "browser_artifacts");
const profileRoot = process.env.HOST_BROWSER_PROFILE_ROOT || path.join(__dirname, "..", "data", "host_profiles");
const appUrl = (process.env.HOST_BROWSER_APP_URL || "http://127.0.0.1:8899").replace(/\/+$/, "");
const isWindows = process.platform === "win32";
const isLinux = process.platform === "linux";

fs.mkdirSync(artifactDir, { recursive: true });
fs.mkdirSync(profileRoot, { recursive: true });
const debugBrowserEnsurePromises = new Map();

function sendJson(res, status, body) {
  const raw = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "access-control-allow-headers": "content-type",
  });
  res.end(raw);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let raw = "";
    req.on("data", (chunk) => {
      raw += chunk;
      if (raw.length > 1024 * 1024) {
        reject(new Error("body too large"));
        req.destroy();
      }
    });
    req.on("end", () => {
      if (!raw.trim()) return resolve({});
      try {
        resolve(JSON.parse(raw));
      } catch (error) {
        reject(error);
      }
    });
    req.on("error", reject);
  });
}

function normalizeExtensionText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function toSafeFileStem(value, fallback = "facebook_extract") {
  const cleaned = normalizeExtensionText(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 48);
  return cleaned || fallback;
}

function saveFacebookExtensionExtract(payload) {
  const query = normalizeExtensionText(payload?.query || payload?.profile?.query || "");
  const items = Array.isArray(payload?.items) ? payload.items : [];
  const topItems = items.slice(0, 5).map((item, index) => ({
    index,
    link: String(item?.link || item?.url || ""),
    text: normalizeExtensionText(item?.text || "").slice(0, 240),
    image_text: normalizeExtensionText(item?.image_text || "").slice(0, 240),
  }));
  const record = {
    saved_at: new Date().toISOString(),
    source: "facebook_post_bridge_extension",
    title: normalizeExtensionText(payload?.title || ""),
    page_url: normalizeExtensionText(payload?.url || ""),
    query,
    count: Number(payload?.count || items.length || 0),
    items: items.map((item) => ({
      text: normalizeExtensionText(item?.text || ""),
      link: normalizeExtensionText(item?.link || item?.url || ""),
      image_text: normalizeExtensionText(item?.image_text || ""),
    })),
  };
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const filename = `${stamp}_${toSafeFileStem(query || record.title || "facebook_extract")}.json`;
  const outputPath = path.join(artifactDir, filename);
  fs.writeFileSync(outputPath, JSON.stringify(record, null, 2), "utf8");
  return {
    ok: true,
    saved: true,
    path: outputPath,
    query,
    count: record.count,
    top_items: topItems,
  };
}

function findBrowserExe(browser = "chrome") {
  const key = String(browser || "chrome").toLowerCase();
  if (process.platform !== "win32") {
    const linuxMap = {
      chrome: ["google-chrome-stable", "google-chrome", "chromium", "chromium-browser", "brave-browser", "brave", "microsoft-edge"],
      edge: ["microsoft-edge", "microsoft-edge-stable"],
      msedge: ["microsoft-edge", "microsoft-edge-stable"],
      firefox: ["firefox"],
      brave: ["brave-browser", "brave"],
    };
    for (const command of linuxMap[key] || linuxMap.chrome) {
      const probe = spawnSync("bash", ["-lc", `command -v ${command}`], { encoding: "utf8" });
      const resolved = probe.status === 0 ? String(probe.stdout || "").trim() : "";
      if (resolved && fs.existsSync(resolved)) return resolved;
    }
  }
  const map = {
    chrome: [
      `${process.env.ProgramFiles}\\Google\\Chrome\\Application\\chrome.exe`,
      `${process.env["ProgramFiles(x86)"]}\\Google\\Chrome\\Application\\chrome.exe`,
      `${process.env.LOCALAPPDATA}\\Google\\Chrome\\Application\\chrome.exe`,
      `${process.env.ProgramFiles}\\BraveSoftware\\Brave-Browser\\Application\\brave.exe`,
      `${process.env["ProgramFiles(x86)"]}\\BraveSoftware\\Brave-Browser\\Application\\brave.exe`,
      `${process.env.LOCALAPPDATA}\\BraveSoftware\\Brave-Browser\\Application\\brave.exe`,
      `${process.env.ProgramFiles}\\Microsoft\\Edge\\Application\\msedge.exe`,
      `${process.env["ProgramFiles(x86)"]}\\Microsoft\\Edge\\Application\\msedge.exe`,
      `${process.env.LOCALAPPDATA}\\Microsoft\\Edge\\Application\\msedge.exe`,
    ],
    edge: [
      `${process.env.ProgramFiles}\\Microsoft\\Edge\\Application\\msedge.exe`,
      `${process.env["ProgramFiles(x86)"]}\\Microsoft\\Edge\\Application\\msedge.exe`,
      `${process.env.LOCALAPPDATA}\\Microsoft\\Edge\\Application\\msedge.exe`,
    ],
    msedge: [
      `${process.env.ProgramFiles}\\Microsoft\\Edge\\Application\\msedge.exe`,
      `${process.env["ProgramFiles(x86)"]}\\Microsoft\\Edge\\Application\\msedge.exe`,
      `${process.env.LOCALAPPDATA}\\Microsoft\\Edge\\Application\\msedge.exe`,
    ],
    firefox: [
      `${process.env.ProgramFiles}\\Mozilla Firefox\\firefox.exe`,
      `${process.env["ProgramFiles(x86)"]}\\Mozilla Firefox\\firefox.exe`,
    ],
    brave: [
      `${process.env.ProgramFiles}\\BraveSoftware\\Brave-Browser\\Application\\brave.exe`,
      `${process.env["ProgramFiles(x86)"]}\\BraveSoftware\\Brave-Browser\\Application\\brave.exe`,
      `${process.env.LOCALAPPDATA}\\BraveSoftware\\Brave-Browser\\Application\\brave.exe`,
    ],
  };
  const candidates = map[key] || map.chrome;
  for (const candidate of candidates) {
    if (candidate && fs.existsSync(candidate)) return candidate;
  }
  throw new Error(`No installed executable found for browser '${browser}'`);
}

function runPowerShell(script) {
  return new Promise((resolve, reject) => {
    const child = spawn("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], {
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString("utf8"); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString("utf8"); });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) {
        resolve(stdout.trim());
      } else {
        reject(new Error((stderr || stdout || `powershell exited ${code}`).trim()));
      }
    });
  });
}

function runCommand(command, args = [], options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      windowsHide: true,
      ...options,
    });
    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (chunk) => { stdout += chunk.toString("utf8"); });
    child.stderr?.on("data", (chunk) => { stderr += chunk.toString("utf8"); });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) {
        resolve(stdout.trim());
      } else {
        reject(new Error((stderr || stdout || `${command} exited ${code}`).trim()));
      }
    });
  });
}

function getLinuxDisplayEnv() {
  const env = { ...process.env };
  env.XDG_RUNTIME_DIR = env.XDG_RUNTIME_DIR || `/run/user/${process.getuid?.() || os.userInfo().uid}`;
  if (!env.DBUS_SESSION_BUS_ADDRESS) {
    const busPath = path.join(env.XDG_RUNTIME_DIR, "bus");
    if (fs.existsSync(busPath)) env.DBUS_SESSION_BUS_ADDRESS = `unix:path=${busPath}`;
  }
  const runtimeDir = env.XDG_RUNTIME_DIR;
  const detectCurrentXwayland = () => {
    try {
      const probe = spawnSync("bash", ["-lc", "ps -eo user=,args= | rg '^\\s*\"$(id -un)\"\\s+.*Xwayland :[0-9]+' || true"], { encoding: "utf8" });
      const lines = String(probe.stdout || "")
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
      for (let i = lines.length - 1; i >= 0; i -= 1) {
        const line = lines[i];
        const match = line.match(/Xwayland\s+(:\d+).*?\s-auth\s+(\S+)/);
        if (match && fs.existsSync(match[2])) {
          return { display: match[1], xauthority: match[2] };
        }
      }
    } catch {}
    return null;
  };
  const detected = detectCurrentXwayland();
  if (detected?.display) env.DISPLAY = detected.display;
  if (detected?.xauthority) env.XAUTHORITY = detected.xauthority;
  env.DISPLAY = env.DISPLAY || ":0";
  if (!env.XAUTHORITY) {
    try {
      const candidates = fs.readdirSync(runtimeDir)
        .filter((name) => name.startsWith(".mutter-Xwaylandauth.") || name === ".Xauthority")
        .map((name) => path.join(runtimeDir, name))
        .filter((candidate) => fs.existsSync(candidate))
        .sort((left, right) => {
          try {
            return fs.statSync(right).mtimeMs - fs.statSync(left).mtimeMs;
          } catch {
            return 0;
          }
        });
      if (candidates[0]) env.XAUTHORITY = candidates[0];
    } catch {}
    if (!env.XAUTHORITY) {
      const homeAuth = path.join(os.homedir(), ".Xauthority");
      if (fs.existsSync(homeAuth)) env.XAUTHORITY = homeAuth;
    }
  }
  return env;
}

function clearDebugProfileSingletons(profileDir) {
  for (const name of ["SingletonCookie", "SingletonLock", "SingletonSocket"]) {
    try {
      fs.rmSync(path.join(profileDir, name), { force: true, recursive: true });
    } catch {}
  }
}

function pythonCodeEval(code = "") {
  return runCommand("python3", ["-c", String(code || "")], {
    env: isLinux ? getLinuxDisplayEnv() : { ...process.env },
  });
}

async function listBrowsers() {
  if (isLinux) {
    const raw = await runCommand("bash", ["-lc", "ps -eo pid=,comm=,args= | rg '(brave|chrome|chromium|firefox|vivaldi|opera|microsoft-edge)' || true"]);
    const items = String(raw || "")
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const match = line.match(/^(\d+)\s+(\S+)\s+(.*)$/);
        if (!match) return null;
        return {
          pid: Number(match[1] || 0),
          name: String(match[2] || ""),
          title: "",
          main_window_handle: "",
          visible: true,
          start_time: "",
          path: "",
          args: String(match[3] || ""),
        };
      })
      .filter(Boolean);
    return items;
  }
  const script = `
$ErrorActionPreference='SilentlyContinue'
$names = @('chrome','msedge','firefox','brave','vivaldi','opera')
$items = Get-Process | Where-Object { $names -contains $_.ProcessName } | ForEach-Object {
  $visible = $false
  if ($_.MainWindowHandle -and $_.MainWindowHandle -ne 0) { $visible = $true }
  $startTime = ''
  $procPath = ''
  try { $startTime = $_.StartTime.ToString('o') } catch {}
  try { $procPath = $_.Path } catch {}
  [pscustomobject]@{
    pid = $_.Id
    name = $_.ProcessName
    title = $_.MainWindowTitle
    main_window_handle = [string]$_.MainWindowHandle
    visible = $visible
    start_time = $startTime
    path = $procPath
  }
}
$items | ConvertTo-Json -Depth 6 -Compress
`;
  const raw = await runPowerShell(script);
  if (!raw) return [];
  const parsed = JSON.parse(raw);
  return Array.isArray(parsed) ? parsed : [parsed];
}

async function focusBrowserWindow(browser = "brave") {
  if (isLinux) {
    const items = await listBrowsers();
    const lower = String(browser || "").toLowerCase();
    const visible = items.find((item) => String(item.name || "").toLowerCase().includes(lower) || String(item.args || "").toLowerCase().includes(lower));
    return { focused: false, pid: Number(visible?.pid || 0), reason: "linux-focus-not-implemented" };
  }
  const items = await listBrowsers();
  const lower = String(browser || "").toLowerCase();
  const candidates = items.filter((item) => String(item.name || "").toLowerCase() === lower);
  const visible = candidates.find((item) => item.visible && Number(item.pid || 0) > 0) || candidates.find((item) => Number(item.pid || 0) > 0);
  if (!visible) {
    return { focused: false };
  }
  const pid = Number(visible.pid || 0);
  const script = `
$ErrorActionPreference='Stop'
$wshell = New-Object -ComObject WScript.Shell
$ok = $wshell.AppActivate(${pid})
[pscustomobject]@{ focused = [bool]$ok; pid = ${pid} } | ConvertTo-Json -Compress
`;
  try {
    const raw = await runPowerShell(script);
    const parsed = JSON.parse(raw || "{}");
    return { focused: Boolean(parsed.focused), pid };
  } catch {
    return { focused: false, pid };
  }
}

function openUrl(url, browser = "") {
  if (!url) throw new Error("url is required");
  if (isLinux && !browser) {
    const child = spawn("xdg-open", [url], { detached: true, stdio: "ignore" });
    child.unref();
    return { opened: url, browser: "default" };
  }
  if (!browser) {
    const child = spawn("cmd", ["/c", "start", "", url], { detached: true, windowsHide: true });
    child.unref();
    return { opened: url, browser: "default" };
  }
  const exe = findBrowserExe(browser);
  const child = spawn(exe, [url], { detached: true, windowsHide: false });
  child.on("error", () => {});
  child.unref();
  return { opened: url, browser, executable: exe };
}

async function openCurrentUrl(url, browser = "brave") {
  if (!url) throw new Error("url is required");
  const exe = findBrowserExe(browser);
  const focus = await focusBrowserWindow(browser);
  const child = spawn(exe, [url], { detached: true, windowsHide: false });
  child.on("error", () => {});
  child.unref();
  return {
    opened: url,
    browser,
    executable: exe,
    reused_existing_window: Boolean(focus.focused),
    focused_pid: Number(focus.pid || 0),
  };
}

function launchDebugBrowser({ browser = "chrome", url = "about:blank", debugPort = 9222, profileDir = "" }) {
  const exe = findBrowserExe(browser);
  const actualProfileDir = profileDir || path.join(profileRoot, `${browser}-debug-${debugPort}`);
  fs.mkdirSync(actualProfileDir, { recursive: true });
  clearDebugProfileSingletons(actualProfileDir);
  const args = [
    `--remote-debugging-port=${debugPort}`,
    `--user-data-dir=${actualProfileDir}`,
    "--password-store=basic",
    "--no-first-run",
    ...(isLinux ? ["--ozone-platform=x11"] : []),
    "--new-window",
    url,
  ];
  const child = spawn(exe, args, {
    detached: true,
    stdio: "ignore",
    windowsHide: false,
    env: isLinux ? getLinuxDisplayEnv() : { ...process.env },
  });
  child.on("error", () => {});
  child.unref();
  return {
    browser,
    executable: exe,
    url,
    remote_debugging_url: `http://127.0.0.1:${debugPort}`,
    profile_dir: actualProfileDir,
  };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function readFacebookSearchProgress(page) {
  const result = await page.call("Runtime.evaluate", {
    expression: `
(() => {
  const bodyText = (document.body?.innerText || '').toLowerCase();
  const articleCount = document.querySelectorAll('div[role="article"]').length;
  const storyMessageCount = document.querySelectorAll('[data-ad-rendering-role="story_message"], div[data-ad-preview="message"], div[data-ad-comet-preview="message"]').length;
  const linkCount = document.querySelectorAll([
    'a[href*="/posts/"]',
    'a[href*="/permalink/"]',
    'a[href*="story_fbid="]',
    'a[href*="/search/posts/"][href*="__cft__"]',
    'a[href*="__cft__"][href*="__tn__="]',
  ].join(', ')).length;
  const feedTextLen = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim().length;
  return {
    href: location.href,
    title: document.title,
    readyState: document.readyState,
    articleCount,
    storyMessageCount,
    linkCount,
    feedTextLen,
    needsLogin: location.href.includes('login') || bodyText.includes('log in') || bodyText.includes('đăng nhập'),
  };
})()
    `,
    returnByValue: true,
    awaitPromise: true,
  });
  return result.result?.value || {};
}

async function waitForFacebookSearchSettled(page, {
  minWaitMs = 2500,
  maxWaitMs = 20000,
  pollMs = 1200,
  stableRounds = 2,
  scroll = false,
} = {}) {
  const startedAt = Date.now();
  let lastSignal = "";
  let stableCount = 0;
  let best = {};
  while ((Date.now() - startedAt) < maxWaitMs) {
    if (scroll) {
      await page.call("Runtime.evaluate", {
        expression: "window.scrollBy(0, Math.max(window.innerHeight * 0.85, 650)); 'ok';",
        returnByValue: true,
        awaitPromise: true,
      });
    }
    await sleep(pollMs);
    const snapshot = await readFacebookSearchProgress(page);
    best = snapshot;
    const signal = [
      snapshot.readyState || "",
      Number(snapshot.articleCount || 0),
      Number(snapshot.storyMessageCount || 0),
      Number(snapshot.linkCount || 0),
      Math.floor(Number(snapshot.feedTextLen || 0) / 120),
      String(snapshot.title || "").slice(0, 80),
      Boolean(snapshot.needsLogin),
    ].join("|");
    if (signal === lastSignal) {
      stableCount += 1;
    } else {
      stableCount = 0;
      lastSignal = signal;
    }
    const elapsed = Date.now() - startedAt;
    const hasCandidates =
      Number(snapshot.articleCount || 0) > 0 ||
      Number(snapshot.storyMessageCount || 0) > 0 ||
      Number(snapshot.linkCount || 0) > 0;
    const titleReady = String(snapshot.title || "").trim().length > 0;
    const hrefReady = String(snapshot.href || "").includes("facebook.com");
    if (elapsed >= minWaitMs && (snapshot.needsLogin || ((hasCandidates || titleReady || hrefReady) && stableCount >= stableRounds))) {
      break;
    }
  }
  return best;
}

async function eagerFacebookScrollBurst(page, rounds = 2) {
  const total = Math.max(1, Math.min(Number(rounds || 2), 4));
  for (let i = 0; i < total; i += 1) {
    try {
      await page.call("Runtime.evaluate", {
        expression: "window.scrollBy(0, Math.max(window.innerHeight * 1.15, 1100)); 'ok';",
        returnByValue: true,
        awaitPromise: true,
      });
    } catch {}
    await sleep(180);
  }
}

function normalizeText(value = "") {
  return String(value || "")
    .normalize("NFD")
    .replace(/\p{Diacritic}+/gu, "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

function uniqueStrings(values) {
  const seen = new Set();
  const out = [];
  for (const value of values) {
    const clean = String(value || "").trim();
    const key = clean.toLowerCase();
    if (!clean || seen.has(key)) continue;
    seen.add(key);
    out.push(clean);
  }
  return out;
}

async function withTimeout(promise, ms = 15000, label = "timeout") {
  let timer = null;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(label)), Math.max(1, Number(ms || 15000)));
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

function selectSoftTargetItems(items, targetCount = 5) {
  const target = Math.max(1, Math.min(20, Number(targetCount || 5)));
  const hardCap = Math.min(20, Math.max(target + 4, Math.ceil(target * 1.6)));
  const picked = [];
  let baselineScore = null;
  let baselineBucket = null;
  for (const item of items) {
    if (!item) continue;
    const itemScore = Number(item.llm_score || item.score || 0);
    const itemBucket = String(item.time_bucket || "none");
    if (picked.length < target) {
      picked.push(item);
      if (picked.length === target) {
        baselineScore = itemScore;
        baselineBucket = itemBucket;
      }
      continue;
    }
    if (picked.length >= hardCap) break;
    const scoreClose = baselineScore === null ? false : itemScore >= (baselineScore - 2);
    const sameBucket = baselineBucket && itemBucket === baselineBucket;
    const strongRelevant = Boolean(item.relevant) && itemScore >= Math.max(8, (baselineScore || 0) - 1);
    if (scoreClose || sameBucket || strongRelevant) {
      picked.push(item);
      continue;
    }
    break;
  }
  return picked.length ? picked : items.slice(0, target);
}

function extractFacebookFamilyKey(item = {}) {
  const candidates = [String(item.url || ""), String(item.raw_url || "")].filter(Boolean);
  for (const value of candidates) {
    const permalink = value.match(/facebook\.com\/groups\/([^/]+)\/permalink\/(\d+)/i);
    if (permalink) return `group:${permalink[1]}:${permalink[2]}`;
    const story = value.match(/[?&]story_fbid=([^&]+)/i);
    if (story) return `story:${story[1]}`;
    const post = value.match(/facebook\.com\/[^/]+\/posts\/([^/?&#]+)/i);
    if (post) return `post:${post[1]}`;
    const groupSet = value.match(/[?&]set=gm\.(\d+)/i);
    if (groupSet) return `groupset:${groupSet[1]}`;
    const fbid = value.match(/[?&]fbid=(\d+)/i);
    if (fbid) return `fbid:${fbid[1]}`;
  }
  return "";
}

function dedupeOrderedFacebookItems(items = []) {
  const picked = [];
  const seen = new Set();
  const familySeen = new Set();
  for (const item of items) {
    if (!item) continue;
    const familyKey = extractFacebookFamilyKey(item);
    if (familyKey && familySeen.has(familyKey)) continue;
    const key = [
      String(item.url || item.raw_url || "").trim(),
      normalizeText(String(item.excerpt || item.text || "")).slice(0, 160),
    ].join("::");
    if (!key || seen.has(key)) continue;
    seen.add(key);
    if (familyKey) familySeen.add(familyKey);
    picked.push(item);
  }
  return picked;
}

function isStrongResolvedFacebookItem(item = {}) {
  const linkSource = String(item.link_source || "");
  const usableUrl = isUsableFacebookResultUrl(String(item.url || "")) || isUsableFacebookResultUrl(String(item.raw_url || ""));
  const text = cleanFacebookDisplayText(String(item.text || item.excerpt || ""), 1800);
  if (!usableUrl) return false;
  if (/resolved_permalink|clicked_anchor|fallback_tab|direct_href|comment_new_tab|detail_new_tab/.test(linkSource)) {
    return text.length >= 120;
  }
  return false;
}

function isDetailResolvedFacebookItem(item = {}) {
  if (!Boolean(item?.resolved_ok)) return false;
  if (!isUsableFacebookResultUrl(String(item.url || "")) && !isUsableFacebookResultUrl(String(item.raw_url || ""))) return false;
  const textSource = String(item.text_source || "");
  const text = cleanFacebookDetailBodyText(String(item.text || item.excerpt || ""));
  return (textSource === "detail_body" || textSource === "photo_ocr") && text.length >= 120;
}

function scoreFacebookResolverCandidate(item = {}, query = "") {
  const text = buildFacebookModelText(item, 700);
  const hay = normalizeText(text);
  const profile = detectIntentProfile(query);
  const signals = getKeywordMatchSignals(item, profile);
  const needles = uniqueStrings([
    ...normalizeText(query).split(/\s+/).filter((token) => token && token.length >= 3),
    ...(Array.isArray(profile.anchorTerms) ? profile.anchorTerms : []),
    ...(Array.isArray(profile.locationAliases) ? profile.locationAliases : []),
  ]);
  let overlap = 0;
  for (const token of needles) {
    if (hay.includes(token)) overlap += 1;
  }
  let score = Number(item.score || 0);
  if (isUsableFacebookResultUrl(String(item.raw_url || "")) || isUsableFacebookResultUrl(String(item.url || ""))) score += 2;
  if (text.length >= 120) score += 1.5;
  if (text.length >= 240) score += 1.5;
  score += Math.min(4, overlap);
  if (signals.roleHit) score += 3;
  if (signals.seniorityHit) score += 2.5;
  if (signals.locationHit) score += 1;
  if (signals.jobHit) score += 1.5;
  if (signals.imageHeavy) score += 1.5;
  if (signals.negativeSeniority) score -= 3;
  if (signals.seekerPost) score -= 5;
  if (Boolean(item.ocr_fallback_used)) score -= 1;
  return score;
}

function cleanFacebookDisplayText(value = "", maxLen = 220) {
  const cleaned = String(value || "")
    .replace(/[\u{1D400}-\u{1D7FF}]/gu, " ")
    .replace(/[\uFFFD\u200B-\u200D\u2060]/g, " ")
    .replace(/[`*_#~<>[\](){}|\\]/g, " ")
    .replace(/\bFacebook(?:\s+Facebook)+\b/gi, "Facebook")
    .replace(/https?:\/\/\S+/gi, " ")
    .replace(/\b(?:Like|Comment|Share|Join|Thích|Bình luận|Chia sẻ)\b/gi, " ")
    .replace(/\b\S*\.comGiang\S*\b/gi, " ")
    .replace(/\b[a-z0-9]{18,}\b/gi, " ")
    .replace(/([A-Za-z0-9])\1{5,}/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return cleaned.slice(0, maxLen);
}

function looksBadFacebookDisplayTitle(value = "") {
  const text = cleanFacebookDisplayText(String(value || ""), 240);
  if (!text) return true;
  if (text.length > 110) return true;
  if (/[`*_#~[\](){}]/.test(String(value || ""))) return true;
  if ((text.match(/[.]/g) || []).length >= 4) return true;
  if ((text.match(/[^\p{L}\p{N}\s/&:+-]/gu) || []).length >= 8) return true;
  const lower = text.toLowerCase();
  if (lower.includes("facebook") && text.length < 28) return true;
  if (/^(intern|devops|cloud|platform|hcm|bai \d+)$/i.test(text)) return true;
  return false;
}

function buildFacebookDisplayTitle(item = {}, index = 0) {
  const author = cleanFacebookDisplayText(String(item.author || ""), 120);
  const summary = cleanFacebookDisplayText(String(item.excerpt || item.text || ""), 220);
  if (!looksBadFacebookDisplayTitle(author)) return author;
  const firstSentence = summary
    .split(/(?<=[.!?])\s+/)
    .map((part) => cleanFacebookDisplayText(part, 100))
    .find((part) => part && part.length >= 18 && part.length <= 100);
  if (firstSentence) return firstSentence;
  const firstChunk = cleanFacebookDisplayText(summary.split(/\s+/).slice(0, 12).join(" "), 90);
  if (firstChunk && firstChunk.length >= 16) return firstChunk;
  return `Bài ${index + 1}`;
}

function buildFacebookModelText(item = {}, maxLen = 900) {
  const bodyText = cleanFacebookDetailBodyText(String(item.body_text_clean || ""));
  if (bodyText && bodyText.length >= 120) {
    return bodyText.slice(0, maxLen).trim();
  }
  const parts = [
    cleanFacebookDisplayText(String(item.author || ""), 120),
    cleanFacebookDisplayText(String(item.excerpt || ""), 380),
    cleanFacebookDisplayText(String(item.text || ""), 700),
  ].filter(Boolean);
  const merged = uniqueStrings(parts).join("\n");
  return merged.slice(0, maxLen).trim();
}

function isNoisyFacebookCandidate(item = {}) {
  const text = normalizeText(buildFacebookModelText(item, 1800));
  if (!text) return true;
  if (hasGarbledDisplayText(`${item.author || ""}\n${item.excerpt || ""}\n${item.text || ""}`)) return true;
  if (/tong hop thong tin tuyen dung|goc nghe nghiep|forum truong|box viec lam|săn job|san job|tong hop nhanh|co hoi nghe nghiep|viec lam thuc tap\/32/.test(text)) {
    return true;
  }
  if (/ccna|tuyen sinh lop|khoa hoc|dao tao|workshop|seminar|webinar|chung chi/.test(text)) {
    return true;
  }
  if (/graphic designer|marketing intern|product owner intern|manual tester|solution sales|business analyst/.test(text) && !/devops|cloud|platform|sre|infra/.test(text)) {
    return true;
  }
  const numberedBullets = (text.match(/(?:^|\s)(?:[1-9]|10)[.)]/g) || []).length;
  if (numberedBullets >= 5 && !/devops intern|thuc tap devops|tuyen devops intern/.test(text)) {
    return true;
  }
  return false;
}

function buildFacebookModelCandidate(item = {}) {
  return {
    ...item,
    author: cleanFacebookDisplayText(String(item.author || ""), 120),
    excerpt: cleanFacebookDisplayText(String(item.excerpt || item.text || ""), 260),
    text: buildFacebookModelText(item, 900),
    time_hint: cleanFacebookDisplayText(String(item.time_hint || ""), 120),
    query_used: cleanFacebookDisplayText(String(item.query_used || ""), 120),
  };
}

function facebookTextOverlapScore(left = "", right = "") {
  const tokenize = (value) => normalizeText(value).split(/\s+/).filter((token) => token && token.length >= 3);
  const leftTokens = new Set(tokenize(left));
  const rightTokens = new Set(tokenize(right));
  if (!leftTokens.size || !rightTokens.size) return 0;
  let shared = 0;
  for (const token of leftTokens) {
    if (rightTokens.has(token)) shared += 1;
  }
  return shared / Math.max(1, Math.min(leftTokens.size, rightTokens.size));
}

function isLikelyWrongResolvedBody(body = "", expected = "") {
  const cleanBody = cleanFacebookDetailBodyText(String(body || ""));
  const cleanExpected = cleanFacebookDisplayText(String(expected || ""), 500);
  if (!cleanBody) return true;
  if (/^unread\b/i.test(cleanBody)) return true;
  if (/nh[oó]m sinh vi[eê]n|vstep|mini hippo/i.test(cleanBody) && !/nh[oó]m sinh vi[eê]n|vstep|mini hippo/i.test(cleanExpected)) {
    return true;
  }
  if (!cleanExpected || cleanExpected.length < 40) return false;
  return facebookTextOverlapScore(cleanBody, cleanExpected) < 0.18;
}

function hasGarbledDisplayText(value = "") {
  const text = String(value || "");
  if (!text) return false;
  const alnumRuns = text.match(/\b[a-z0-9]{18,}\b/gi) || [];
  const mathBold = (text.match(/[\u{1D400}-\u{1D7FF}]/gu) || []).length;
  const replacement = (text.match(/[\uFFFD]/g) || []).length;
  return alnumRuns.length >= 2 || mathBold >= 8 || replacement >= 2;
}

function describeKeepReason(item = {}) {
  if (item.llm_reason) return String(item.llm_reason);
  const labels = {
    "job-signal": "đúng bài tuyển",
    "intern-signal": "đúng tín hiệu intern/fresher",
    "devops-signal": "đúng role DevOps/cloud/platform",
    "location-signal": "đúng khu vực yêu cầu",
    "recruiter-voice": "giọng điệu bài tuyển thật",
    "author-recruiting": "nguồn đăng thiên về tuyển dụng",
    "unknown-age": "chưa đọc chắc ngày nhưng nội dung khớp",
    "month-ish": "có tín hiệu còn tương đối mới",
  };
  const reasons = Array.isArray(item.reasons) ? item.reasons.map((reason) => labels[reason] || "").filter(Boolean) : [];
  return reasons.slice(0, 3).join(", ");
}

function shouldOpenFacebookDetail(item = {}, scoreInfo = {}, timeWindow = null) {
  if (item.url) return false;
  if (!item.raw_url) return false;
  const score = Number(scoreInfo.score || 0);
  if (!Number.isFinite(score) || score < 4) return false;
  const recency = item.recency_hours;
  if (timeWindow && recency !== null && recency !== undefined && recency !== "") {
    const hours = Number(recency);
    if (Number.isFinite(hours) && hours > Number(timeWindow.maxHours || 0)) return false;
  }
  return true;
}

function isFacebookPermalinkLike(url = "") {
  const value = String(url || "").trim();
  if (!value || !/facebook\.com/i.test(value)) return false;
  return (
    /facebook\.com\/groups\/[^/]+\/permalink\//i.test(value) ||
    /facebook\.com\/[^/]+\/posts\//i.test(value) ||
    /facebook\.com\/permalink\.php\?/i.test(value) ||
    /facebook\.com\/share\/p\//i.test(value) ||
    /story_fbid=/i.test(value)
  );
}

function isFacebookPhotoUrl(url = "") {
  const value = String(url || "").trim();
  if (!value || !/facebook\.com/i.test(value)) return false;
  return /facebook\.com\/photo\/\?/i.test(value) || (/[?&]fbid=/i.test(value) && /[?&]set=gm\./i.test(value));
}

function sanitizeFacebookSearchQuery(rawQuery = "") {
  let query = String(rawQuery || "").trim();
  if (!query) return "";
  const patterns = [
    /^\s*(giúp\s+mình|giup minh|giúp tôi|giup toi|cho mình|cho tôi|tim giup toi|tìm giúp tôi|tim giup minh|tìm giúp mình)\s+/i,
    /^\s*(tìm\s+cho\s+tôi|tim cho toi|tìm\s+cho\s+mình|tim cho minh)\s+/i,
    /^\s*(lên|len|vào|vao|mở|mo)\s+(facebook|face)\s+/i,
    /^\s*(tìm|tim|search|research|kiếm|kiem)\s+/i,
    /^\s*\d+\s*(bài viết|bai viet|bài|post|posts)\s+/i,
    /\s*(trên|tren|ở|o)\s+(facebook|face)\s*/gi,
    /\s*(bài viết|bai viet|bài|post|posts)\s*/gi,
    /\s*\b(cho tôi|cho toi|cho mình|cho minh)\b\s*/gi,
  ];
  for (const pattern of patterns) {
    query = query.replace(pattern, " ");
  }
  query = query.replace(/^\s*\d+\s+/i, " ");
  query = query.replace(/\s+/g, " ").trim().replace(/^[ .,:;-]+|[ .,:;-]+$/g, "");
  return query || String(rawQuery || "").trim();
}

function buildFacebookSearchVariants(rawQuery = "") {
  const cleaned = sanitizeFacebookSearchQuery(rawQuery);
  const normalized = normalizeText(cleaned);
  return uniqueStrings([cleaned, normalized]).filter(Boolean);
}

function facebookQueryTokens(rawQuery = "") {
  const normalized = normalizeText(sanitizeFacebookSearchQuery(rawQuery))
    .replace(/\b(thuc tap sinh|thuc tap|intern|fresher|junior|new grad|entry level)\b/g, " entrylevel ")
    .replace(/\b(devops|sre|cloud|platform engineer|platform|infra|infrastructure|sysadmin)\b/g, " devopsfamily ")
    .replace(/\b(hcm|tphcm|tp hcm|ho chi minh|hcmc|sai gon)\b/g, " hcmfamily ")
    .replace(/\b(tuyen dung|tuyen|viec lam|hiring|recruit)\b/g, " hiringfamily ");
  return uniqueStrings(
    normalized
      .split(/\s+/)
      .map((item) => item.trim())
      .filter((item) => item && item.length >= 2),
  );
}

function querySimilarityScore(leftQuery = "", rightQuery = "") {
  const left = new Set(facebookQueryTokens(leftQuery));
  const right = new Set(facebookQueryTokens(rightQuery));
  if (!left.size || !right.size) return 0;
  let overlap = 0;
  for (const token of left) {
    if (right.has(token)) overlap += 1;
  }
  return overlap / Math.max(left.size, right.size);
}

function diversifyFacebookQueries(queries = [], maxQueries = 6) {
  const cleaned = uniqueStrings((queries || []).map((item) => sanitizeFacebookSearchQuery(item)).filter(Boolean));
  const selected = [];
  const limit = Math.max(1, Number(maxQueries || 6));
  for (const query of cleaned) {
    const tooSimilar = selected.some((picked) => querySimilarityScore(query, picked) >= 0.85);
    if (tooSimilar) continue;
    selected.push(query);
    if (selected.length >= limit) break;
  }
  if (selected.length >= limit) return selected;
  for (const query of cleaned) {
    if (selected.includes(query)) continue;
    selected.push(query);
    if (selected.length >= limit) break;
  }
  return selected;
}

function detectIntentProfile(rawQuery = "") {
  const cleanedRawQuery = sanitizeFacebookSearchQuery(rawQuery);
  const q = normalizeText(cleanedRawQuery);
  const has = (...markers) => markers.some((marker) => q.includes(marker));
  const profile = {
    original_query: String(rawQuery || "").trim(),
    normalized_query: q,
    roleTerms: [],
    seniorityTerms: [],
    locationTerms: [],
    anchorTerms: [],
    locationAliases: [],
    mustIncludeGroups: [],
    wantsRoleMatch: false,
    wantsEntryLevel: false,
    wantsLocation: false,
  };
  const districtAliases = {
    "go vap": ["go vap", "go vap", "gv", "binh thanh", "phu nhuan", "tan binh", "quan 12"],
    "quan 7": ["quan 7", "q7", "phu my hung", "nha be"],
    "thu duc": ["thu duc", "quan 2", "q2", "quan 9", "q9", "tp thu duc"],
    "tan binh": ["tan binh", "san bay", "phu nhuan", "go vap"],
  };

  if (has("devops", "sre", "platform engineer", "cloud engineer", "sysadmin", "site reliability")) {
    profile.roleTerms = ["devops", "sre", "cloud", "platform", "sysadmin"];
    profile.anchorTerms = [...profile.anchorTerms, "devops", "sre", "cloud", "platform", "kubernetes", "docker", "terraform", "cicd"];
    profile.mustIncludeGroups.push(["devops", "sre", "cloud", "platform", "sysadmin"]);
    profile.wantsRoleMatch = true;
  }
  if (has("intern", "thuc tap", "thuc sinh", "fresher", "junior", "khong can kinh nghiem", "không cần kinh nghiệm", "entry level")) {
    profile.seniorityTerms = ["intern", "thực tập", "thực tập sinh", "fresher", "junior", "không cần kinh nghiệm", "entry level"];
    profile.anchorTerms = [...profile.anchorTerms, "intern", "thuc tap", "thuc tap sinh", "fresher", "junior", "khong can kinh nghiem", "entry level"];
    profile.mustIncludeGroups.push(["intern", "thuc tap", "thuc tap sinh", "fresher"]);
    profile.wantsEntryLevel = true;
  }
  if (has("hcm", "tphcm", "tp hcm", "ho chi minh", "sai gon", "hcmc")) {
    profile.locationTerms = ["hcm", "tphcm", "hồ chí minh", "sài gòn"];
    profile.locationAliases = ["hcm", "tphcm", "tp hcm", "ho chi minh", "sai gon", "hcmc"];
    profile.mustIncludeGroups.push(["hcm", "tphcm", "ho chi minh", "sai gon", "hcmc"]);
    profile.wantsLocation = true;
  }
  for (const [district, aliases] of Object.entries(districtAliases)) {
    if (q.includes(district) || aliases.some((alias) => q.includes(alias))) {
      profile.locationTerms = uniqueStrings([...(profile.locationTerms || []), ...aliases.slice(0, 3)]);
      profile.locationAliases = uniqueStrings([...(profile.locationAliases || []), ...aliases]);
      profile.mustIncludeGroups.push(aliases.slice(0, 4));
      profile.wantsLocation = true;
      break;
    }
  }
  if (has("tro", "phong", "thue", "o ghep", "nha tro", "phong tro", "cho thue")) {
    profile.anchorTerms = [...profile.anchorTerms, "nha tro", "phong tro", "cho thue", "o ghep", "studio", "con phong"];
  }
  if (has("mua", "ban", "thanh ly", "pass")) {
    profile.anchorTerms = [...profile.anchorTerms, "mua", "ban", "pass", "thanh ly", "gia", "tinh trang"];
  }
  if (has("review", "danh gia", "trai nghiem")) {
    profile.anchorTerms = [...profile.anchorTerms, "review", "danh gia", "trai nghiem", "uu nhuoc diem"];
  }
  profile.anchorTerms = uniqueStrings(profile.anchorTerms);
  profile.locationAliases = uniqueStrings(profile.locationAliases);
  return profile;
}

function hasAnyPattern(text = "", patterns = []) {
  return patterns.some((pattern) => pattern.test(text));
}

function semanticSignals(hay = "") {
  return {
    roleMatch: hasAnyPattern(hay, [
      /\bdevops\b/, /\bsre\b/, /site reliability/, /cloud engineer/, /platform engineer/,
      /\bsysadmin\b/, /system engineer/, /system administrator/, /kubernetes/, /docker/,
      /\bcicd\b/, /jenkins/, /terraform/, /\baws\b/, /\bgcp\b/, /\bazure\b/,
    ]),
    entryLevelMatch: hasAnyPattern(hay, [
      /\bintern\b/, /thuc tap/, /thuc tap sinh/, /\bfresher\b/, /\bjunior\b/,
      /0\s*[-–]?\s*1\s*nam kinh nghiem/, /0\s*[-–]?\s*2\s*nam kinh nghiem/,
      /khong can kinh nghiem/, /không cần kinh nghiệm/, /duoc dao tao/, /được đào tạo/,
      /training/, /accept fresher/, /welcome fresher/, /new graduate/, /sinh vien nam cuoi/,
    ]),
    jobSignal: hasAnyPattern(hay, [
      /tuyen/, /tuyen dung/, /hiring/, /recruit/, /ung tuyen/, /viec lam/, /career/, /\bjob\b/,
      /jd\b/, /job description/, /gui cv/, /\bapply\b/, /mo vi tri/, /open role/, /opening/,
    ]),
    locationMatch: hasAnyPattern(hay, [
      /\bhcm\b/, /\btphcm\b/, /tp hcm/, /ho chi minh/, /sai gon/, /\bhcmc\b/, /quan 1/, /quan 7/, /thu duc/,
    ]),
    wrongRoleSignal: hasAnyPattern(hay, [
      /tai xe/, /driver/, /ke toan/, /accountant/, /\bba\b/, /business analyst/, /tester/,
      /data analyst/, /data engineer/, /marketing/, /sales/, /solution sales/, /customer support/,
      /security intern/, /microsoft 365/, /\bhr\b/, /nhan su/, /thu ngan/,
    ]),
  };
}

function keywordClusterMatches(hay = "", variants = []) {
  const text = normalizeText(hay);
  return uniqueStrings(variants).some((variant) => {
    const token = normalizeText(variant);
    return token && text.includes(token);
  });
}

function getKeywordMatchSignals(item = {}, profile = {}) {
  const hay = normalizeText(`${item.author || ""}\n${item.text || ""}\n${item.excerpt || ""}\n${item.llm_reason || ""}`);
  const sem = semanticSignals(hay);
  const roleVariants = uniqueStrings([
    ...(Array.isArray(profile.roleTerms) ? profile.roleTerms : []),
    "devops", "sre", "cloud", "platform", "infra", "infrastructure", "sysadmin", "system engineer", "system administrator",
    "kubernetes", "docker", "terraform", "jenkins", "aws", "gcp", "azure",
  ]);
  const seniorityVariants = uniqueStrings([
    ...(Array.isArray(profile.seniorityTerms) ? profile.seniorityTerms : []),
    "intern", "thuc tap", "thuc tap sinh", "fresher", "junior", "entry level", "new grad",
    "khong can kinh nghiem", "không cần kinh nghiệm", "trainee",
  ]);
  const locationVariants = uniqueStrings([
    ...(Array.isArray(profile.locationTerms) ? profile.locationTerms : []),
    ...(Array.isArray(profile.locationAliases) ? profile.locationAliases : []),
  ]);
  const roleHit = sem.roleMatch || keywordClusterMatches(hay, roleVariants);
  const seniorityHit = sem.entryLevelMatch || keywordClusterMatches(hay, seniorityVariants);
  const locationHit = !profile?.wantsLocation || sem.locationMatch || keywordClusterMatches(hay, locationVariants);
  const jobHit = sem.jobSignal || /\bapply\b|gui cv|ung tuyen|job description|jd\b|opening|open role|co hoi viec lam/.test(hay);
  const imageHeavy = Boolean(item.image_bounds && Number(item.image_bounds.width || 0) >= 180 && Number(item.image_bounds.height || 0) >= 180);
  const negativeSeniority = /\bsenior\b|\blead\b|\bmanager\b|\bdirector\b|3 nam kinh nghiem|4 nam kinh nghiem|5 nam kinh nghiem|\b3\+?\s*years?\b|\b4\+?\s*years?\b|\b5\+?\s*years?\b/.test(hay);
  const seekerPost = /minh hien dang tim|mình hiện đang tìm|em dang tim|tim viec|looking for job|xin viec|ung vien/.test(hay);
  return { roleHit, seniorityHit, locationHit, jobHit, imageHeavy, negativeSeniority, seekerPost };
}

function candidatePassesClickGate(item = {}, profile = {}) {
  const signals = getKeywordMatchSignals(item, profile);
  if (signals.seekerPost) return false;
  if (signals.negativeSeniority && !signals.imageHeavy && !signals.roleHit) return false;
  if (profile?.wantsRoleMatch && profile?.wantsEntryLevel) {
    return (signals.roleHit && signals.seniorityHit) || signals.imageHeavy || (signals.roleHit && signals.jobHit);
  }
  if (profile?.wantsRoleMatch) return signals.roleHit || signals.imageHeavy || signals.jobHit;
  if (profile?.wantsEntryLevel) return signals.seniorityHit || signals.imageHeavy || signals.jobHit;
  return signals.roleHit || signals.seniorityHit || signals.jobHit || signals.imageHeavy;
}

function scoreFacebookDisplayCandidate(item = {}, profile = {}, query = "") {
  const base = Number(item.score || 0);
  const keywordScore = Number(scoreFacebookResolverCandidate(item, query) || 0);
  const signals = getKeywordMatchSignals(item, profile);
  let score = base + keywordScore;
  if (signals.roleHit) score += 6;
  if (signals.seniorityHit) score += 4;
  if (signals.locationHit) score += 2;
  if (signals.jobHit) score += 2;
  if (signals.imageHeavy) score += 1.5;
  if (Boolean(item.resolved_ok)) score += 3;
  if (String(item.text_source || "") === "detail_body") score += 3;
  if (String(item.text_source || "") === "photo_ocr") score += 2.5;
  if (isFacebookPhotoUrl(String(item.url || item.raw_url || ""))) score += 1;
  if (isFacebookPermalinkLike(String(item.url || ""))) score += 5;
  if (isFacebookPermalinkLike(String(item.raw_url || ""))) score += 3;
  if (isFacebookPhotoUrl(String(item.url || "")) && !isFacebookPermalinkLike(String(item.url || item.raw_url || ""))) score -= 2;
  if (signals.negativeSeniority) score -= 6;
  if (signals.seekerPost) score -= 8;
  if (isNoisyFacebookCandidate(item)) score -= 9;
  const hay = normalizeText(`${item.author || ""}\n${item.text || ""}\n${item.excerpt || ""}`);
  const hanoiHit = /\bhn\b|ha noi|hà nội|cau giay|dong da|hoan kiem|thanh xuan/.test(hay);
  const hcmHit = /\bhcm\b|\btphcm\b|tp hcm|ho chi minh|sai gon|\bhcmc\b|phu nhuan|binh thanh|thu duc|quan [1-9]/.test(hay);
  if (profile?.wantsLocation && hanoiHit && !hcmHit) score -= 12;
  if (/goc nghe nghiep|t[oô]ng h[oơ]p th[oô]ng tin tuy[eể]n d[uụ]ng|tong hop thong tin tuyen dung|box viec lam|forum truong|tong hop nhanh|tọa độ tìm việc|toa do tim viec/.test(hay)) score -= 12;
  if (/manual tester|tester\b/.test(hay) && !signals.roleHit) score -= 8;
  const text = buildFacebookModelText(item, 1200);
  if (text.length >= 120) score += 1;
  if (text.length >= 260) score += 1;
  return score;
}

function describeLocalKeepReason(item = {}, profile = {}) {
  const signals = getKeywordMatchSignals(item, profile);
  const parts = [];
  if (signals.roleHit) parts.push("đúng nhóm role");
  if (signals.seniorityHit) parts.push("đúng nhóm intern/thực tập");
  if (signals.locationHit) parts.push("có tín hiệu địa điểm");
  if (signals.jobHit) parts.push("có tín hiệu tuyển dụng");
  if (String(item.text_source || "") === "detail_body") parts.push("đọc được nội dung bài");
  if (String(item.text_source || "") === "photo_ocr") parts.push("đọc được nội dung từ ảnh");
  if (!parts.length) parts.push("khớp từ khóa chính");
  return parts.slice(0, 3).join(" · ");
}

function isUsableFacebookResultUrl(url = "") {
  const value = String(url || "").trim();
  if (!value || !/facebook\.com/i.test(value)) return false;
  if (/facebook\.com\/hashtag\//i.test(value)) return false;
  if (/facebook\.com\/search\/top/i.test(value)) return false;
  if (/facebook\.com\/groups\/[^/]+\/?(?:\?|$)/i.test(value) && !/\/permalink\//i.test(value)) return false;
  return true;
}

function buildFacebookQueryPlan(rawQuery = "") {
  const profile = detectIntentProfile(rawQuery);
  const original = sanitizeFacebookSearchQuery(rawQuery);
  const candidates = [];
  const push = (...items) => candidates.push(...items.filter(Boolean));

  if (profile.roleTerms.length && profile.seniorityTerms.length && profile.locationTerms.length) {
    push(
      "tuyen dung devops intern hcm",
      "devops intern hcm",
      "intern devops hcm",
      "thuc tap devops hcm",
      "thuc tap sinh devops ho chi minh",
      "viec lam devops intern tphcm",
      "tuyen devops intern tphcm",
      "devops fresher hcm",
      "cloud intern hcm",
      "platform engineer intern hcm",
    );
  } else if (profile.roleTerms.length && profile.seniorityTerms.length) {
    push(
      "tuyen dung devops intern",
      "devops intern",
      "intern devops",
      "tuyen devops intern",
      "thuc tap devops",
      "devops fresher",
      "cloud intern",
    );
  } else if (profile.roleTerms.length && profile.locationTerms.length) {
    push(
      "tuyen dung devops hcm",
      "viec lam devops hcm",
      "cloud engineer hcm",
      "platform engineer hcm",
    );
  }

  if (normalizeText(original).includes("tuyen") || normalizeText(original).includes("tuyển")) {
    push(...buildFacebookSearchVariants(original.replace(/tuyển/gi, "việc làm").replace(/tuyen/gi, "viec lam")));
  } else if (profile.roleTerms.length || profile.seniorityTerms.length) {
    push(
      `tuyen dung ${original}`.trim(),
      `viec lam ${original}`.trim(),
    );
  }

  push(...buildFacebookSearchVariants(original));

  return {
    profile,
    queries: diversifyFacebookQueries(candidates, 6),
  };
}

function demoModeEnabled() {
  const value = String(process.env.FACEBOOK_DEMO_MODE || "").trim().toLowerCase();
  return value === "1" || value === "true" || value === "yes" || value === "on";
}

function buildFacebookDemoQueries(rawQuery = "") {
  const profile = detectIntentProfile(rawQuery);
  const canonical = [];
  const push = (...items) => canonical.push(...items.filter(Boolean));
  if (profile.roleTerms.length && profile.seniorityTerms.length && profile.locationTerms.length) {
    push(
      "tuyen dung devops intern hcm",
      "devops intern hcm",
      "intern devops hcm",
      "thuc tap devops hcm",
    );
  } else if (profile.roleTerms.length && profile.seniorityTerms.length) {
    push(
      "tuyen dung devops intern",
      "devops intern",
      "intern devops",
      "thuc tap devops",
    );
  } else if (profile.roleTerms.length && profile.locationTerms.length) {
    push(
      "tuyen dung devops hcm",
      "viec lam devops hcm",
      "cloud engineer hcm",
      "platform engineer hcm",
    );
  }
  if (!canonical.length) {
    push(...buildFacebookSearchVariants(rawQuery));
  }
  return uniqueStrings(canonical).slice(0, 4);
}

async function fetchFacebookSearchPlan({ query, maxQueries = 6 }) {
  const fallbackPlan = buildFacebookQueryPlan(query);
  if (!query) return fallbackPlan;
  try {
    const response = await fetchJson(`${appUrl}/api/host-browser/facebook-search-plan`, {
      method: "POST",
      headers: { "content-type": "application/json; charset=utf-8" },
      body: JSON.stringify({
        query: String(query || ""),
        max_queries: Math.max(1, Math.min(10, Number(maxQueries || 6))),
      }),
    });
    const data = response.data || {};
    const profile = data.profile && typeof data.profile === "object" ? data.profile : {};
    const queryVariants = Array.isArray(data.query_variants) ? data.query_variants : [];
    const cleanedBaseQuery = sanitizeFacebookSearchQuery(profile.normalized_query || query);
    const expandedVariants = queryVariants.flatMap((item) => buildFacebookSearchVariants(item));
    return {
      profile: {
        original_query: String(profile.original_query || query || "").trim(),
        normalized_query: String(profile.normalized_query || normalizeText(query)),
        roleTerms: Array.isArray(profile.roleTerms) ? profile.roleTerms : [],
        seniorityTerms: Array.isArray(profile.seniorityTerms) ? profile.seniorityTerms : [],
        locationTerms: Array.isArray(profile.locationTerms) ? profile.locationTerms : [],
        anchorTerms: Array.isArray(profile.anchorTerms) ? profile.anchorTerms : [],
        locationAliases: Array.isArray(profile.locationAliases) ? profile.locationAliases : [],
        mustIncludeGroups: Array.isArray(profile.mustIncludeGroups) ? profile.mustIncludeGroups : [],
      },
      queries: diversifyFacebookQueries([
        ...fallbackPlan.queries,
        ...expandedVariants,
        ...buildFacebookSearchVariants(cleanedBaseQuery),
      ], Math.max(1, Math.min(10, Number(maxQueries || 6)))),
      planner_intent: String(data.intent || "").trim(),
      planner_criteria: Array.isArray(data.criteria) ? data.criteria.map((item) => String(item || "")).filter(Boolean) : [],
      planner_summary: String(data.summary || "").trim(),
    };
  } catch {
    return fallbackPlan;
  }
}

function parseFacebookRecencyHours(value = "") {
  const text = normalizeText(value);
  if (!text) return null;
  if (/just now|vua xong|moi xong|vua dang|moi dang/.test(text)) return 0.1;
  const patterns = [
    { regex: /(\d+)\s*(phut|ph|mins|min)\b/, factor: 1 / 60 },
    { regex: /(\d+)\s*(gio|hour|hours|hr|hrs)\b/, factor: 1 },
    { regex: /(\d+)\s*h\b/, factor: 1 },
    { regex: /(\d+)\s*(ngay|day|days)\b/, factor: 24 },
    { regex: /(\d+)\s*d\b/, factor: 24 },
    { regex: /(\d+)\s*(tuan|week|weeks)\b/, factor: 24 * 7 },
    { regex: /(\d+)\s*(thang|month|months)\b/, factor: 24 * 30 },
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern.regex);
    if (match) return Number(match[1]) * pattern.factor;
  }
  return null;
}

function parseAbsoluteDateHours(value = "", now = new Date()) {
  const raw = String(value || "").trim();
  if (!raw) return null;
  const normalized = normalizeText(raw);
  const currentYear = now.getFullYear();
  const monthMap = {
    january: 0, jan: 0, february: 1, feb: 1, march: 2, mar: 2, april: 3, apr: 3, may: 4, june: 5, jun: 5,
    july: 6, jul: 6, august: 7, aug: 7, september: 8, sep: 8, sept: 8, october: 9, oct: 9, november: 10, nov: 10, december: 11, dec: 11,
  };
  for (const [name, monthIndex] of Object.entries(monthMap)) {
    const match = normalized.match(new RegExp(`\\b${name}\\s+(\\d{1,2})(?:,\\s*(\\d{4}))?`, "i"));
    if (match) {
      const year = match[2] ? Number(match[2]) : currentYear;
      const day = Number(match[1]);
      const parsed = new Date(year, monthIndex, day, 12, 0, 0, 0);
      if (!Number.isNaN(parsed.getTime()) && parsed <= now) {
        return (now.getTime() - parsed.getTime()) / 36e5;
      }
    }
  }
  const vnMatch = normalized.match(/\bngay\s+(\d{1,2})[\/\-](\d{1,2})(?:[\/\-](\d{2,4}))?\b/);
  if (vnMatch) {
    const day = Number(vnMatch[1]);
    const month = Number(vnMatch[2]) - 1;
    let year = vnMatch[3] ? Number(vnMatch[3]) : currentYear;
    if (year < 100) year += 2000;
    const parsed = new Date(year, month, day, 12, 0, 0, 0);
    if (!Number.isNaN(parsed.getTime()) && parsed <= now) {
      return (now.getTime() - parsed.getTime()) / 36e5;
    }
  }
  const slashMatch = normalized.match(/\b(\d{1,2})[\/\-](\d{1,2})(?:[\/\-](\d{2,4}))?\b/);
  if (slashMatch) {
    const day = Number(slashMatch[1]);
    const month = Number(slashMatch[2]) - 1;
    let year = slashMatch[3] ? Number(slashMatch[3]) : currentYear;
    if (year < 100) year += 2000;
    const parsed = new Date(year, month, day, 12, 0, 0, 0);
    if (!Number.isNaN(parsed.getTime()) && parsed <= now) {
      return (now.getTime() - parsed.getTime()) / 36e5;
    }
  }
  return null;
}

function parseFacebookTimeMetadata(value = "", now = new Date()) {
  const recencyHours = parseFacebookRecencyHours(value);
  if (recencyHours !== null) {
    return { recencyHours, parsedFrom: "relative", raw: String(value || "") };
  }
  const absoluteHours = parseAbsoluteDateHours(value, now);
  if (absoluteHours !== null) {
    return { recencyHours: absoluteHours, parsedFrom: "absolute", raw: String(value || "") };
  }
  return { recencyHours: null, parsedFrom: "", raw: String(value || "") };
}

function parseQueryTimeWindow(rawQuery = "") {
  const text = normalizeText(rawQuery);
  if (!text) return null;
  if (/hom nay|today/.test(text)) return { maxHours: 24, label: "today" };
  if (/tuan nay|this week|7 ngay|7 days|1 tuan|mot tuan/.test(text)) return { maxHours: 24 * 7, label: "7d" };
  if (/2 tuan|2 weeks|14 ngay|14 days/.test(text)) return { maxHours: 24 * 14, label: "14d" };
  const daysMatch = text.match(/(\d+)\s*(ngay|days)\b/);
  if (daysMatch) return { maxHours: Number(daysMatch[1]) * 24, label: `${daysMatch[1]}d` };
  const weeksMatch = text.match(/(\d+)\s*(tuan|weeks)\b/);
  if (weeksMatch) return { maxHours: Number(weeksMatch[1]) * 24 * 7, label: `${weeksMatch[1]}w` };
  const monthsMatch = text.match(/(\d+)\s*(thang|months|month|mo|m)\b/);
  if (monthsMatch) return { maxHours: Number(monthsMatch[1]) * 24 * 31, label: `${monthsMatch[1]}m` };
  if (/1 thang|mot thang|30 ngay|30 days|1 month|thang do lai day|thang tro lai day|trong 1 thang/.test(text)) {
    return { maxHours: 24 * 31, label: "1m" };
  }
  return null;
}

function deriveFacebookFilterYears(timeWindow, now = new Date()) {
  if (!timeWindow?.maxHours) return [];
  const start = new Date(now.getTime() - (Number(timeWindow.maxHours || 0) * 36e5));
  const end = new Date(now.getTime());
  const years = [];
  for (let year = start.getFullYear(); year <= end.getFullYear(); year += 1) {
    years.push(String(year));
  }
  return years;
}

function deriveFacebookFilterDateRange(timeWindow, now = new Date()) {
  if (!timeWindow?.maxHours) return null;
  const start = new Date(now.getTime() - (Number(timeWindow.maxHours || 0) * 36e5));
  const end = new Date(now.getTime());
  return { start, end };
}

function buildFacebookCreationTimeFilters(timeWindow, now = new Date()) {
  const range = deriveFacebookFilterDateRange(timeWindow, now);
  if (!range) return "";
  const start = range.start;
  const end = range.end;
  const args = {
    start_year: String(start.getFullYear()),
    start_month: `${start.getFullYear()}-${start.getMonth() + 1}`,
    end_year: String(end.getFullYear()),
    end_month: `${end.getFullYear()}-${end.getMonth() + 1}`,
    start_day: `${start.getFullYear()}-${start.getMonth() + 1}-${start.getDate()}`,
    end_day: `${end.getFullYear()}-${end.getMonth() + 1}-${end.getDate()}`,
  };
  const payload = {
    "rp_creation_time:0": JSON.stringify({
      name: "creation_time",
      args: JSON.stringify(args),
    }),
  };
  return Buffer.from(JSON.stringify(payload), "utf8").toString("base64");
}

function buildFacebookSearchUrl(rawQuery = "", timeWindow = null) {
  const variants = buildFacebookSearchVariants(rawQuery);
  const query = String(variants.find((item) => item === normalizeText(item)) || variants[0] || rawQuery || "").trim();
  const base = `https://www.facebook.com/search/top?q=${encodeURIComponent(query)}`;
  const filters = buildFacebookCreationTimeFilters(timeWindow);
  if (!filters) return base;
  return `${base}&filters=${encodeURIComponent(filters)}`;
}

function facebookHrefHasCreationTimeFilter(href = "", targetYears = []) {
  try {
    const parsed = new URL(String(href || ""));
    const filters = parsed.searchParams.get("filters");
    if (!filters) return false;
    const decoded = Buffer.from(String(filters), "base64").toString("utf8");
    if (!decoded) return false;
    const payload = JSON.parse(decoded);
    const values = Object.values(payload || {});
    const targetList = Array.isArray(targetYears) ? targetYears.map((item) => String(item)) : [];
    if (!targetList.length) return true;
    return values.some((value) => {
      const text = String(value || "");
      return text.includes("creation_time") && targetList.every((year) => text.includes(year));
    });
  } catch {
    return false;
  }
}

function scoreFacebookItem(item, profile) {
  const hay = normalizeText(`${item.author || ""}\n${item.text || ""}\n${item.excerpt || ""}\n${item.time_hint || ""}`);
  let score = 0;
  const reasons = [];
  const signals = semanticSignals(hay);
  const garbled = hasGarbledDisplayText(`${item.author || ""}\n${item.excerpt || ""}\n${item.text || ""}`);

  if (signals.jobSignal) {
    score += 2;
    reasons.push("job-signal");
  }
  if (signals.entryLevelMatch) {
    score += 3;
    reasons.push("intern-signal");
  }
  if (signals.roleMatch) {
    score += 5;
    reasons.push("devops-signal");
  }
  if (signals.locationMatch) {
    score += 2;
    reasons.push("location-signal");
  }
  if (/cong ty.*tuyen|ben minh.*tuyen|dang tuyen|mo vi tri|opening|open role|jd|job description|gui cv|apply|ung tuyen/.test(hay)) {
    score += 4;
    reasons.push("recruiter-voice");
  }
  if (/minh hien dang tim|mình hiện đang tìm|em hien dang tim|em dang tim|mong muon tim|muon tim co hoi|looking for.*intern|tim co hoi|tim viec|tim cong viec|cong viec fresher|can tim job|tha thiet tim|se chu dong gui cv|xin viec|kiem job/.test(hay)) {
    score -= 8;
    reasons.push("candidate-post");
  }
  if (/tuyen dung|recruit|hiring/.test(normalizeText(item.author || "")) && /cong ty.*tuyen|ben minh.*tuyen|dang tuyen|mo vi tri|opening|open role|jd|job description|gui cv|apply|ung tuyen/.test(hay)) {
    score += 2;
    reasons.push("author-recruiting");
  }

  if (profile.wantsRoleMatch && !signals.roleMatch) {
    score -= 12;
    reasons.push("role-mismatch");
  }
  if (profile.wantsEntryLevel && !signals.entryLevelMatch) {
    score -= 3;
    reasons.push("entry-level-missing");
  }
  if (profile.wantsLocation && !signals.locationMatch) {
    score -= 2;
    reasons.push("location-missing");
  }
  if (profile.wantsRoleMatch && signals.wrongRoleSignal && !signals.roleMatch) {
    score -= 12;
    reasons.push("wrong-role");
  }
  if (signals.wrongRoleSignal && signals.roleMatch) {
    score -= 4;
    reasons.push("mixed-role");
  }
  if (garbled) {
    score -= 6;
    reasons.push("garbled-text");
  }

  if (/senior|lead|manager|director|10 nam|5 nam kinh nghiem/.test(hay)) {
    score -= 3;
    reasons.push("seniority-mismatch");
  }

  const timeMeta = parseFacebookTimeMetadata(item.time_hint || hay);
  const recencyHours = timeMeta.recencyHours;
  if (recencyHours !== null) {
    if (recencyHours <= 72) {
      score += 3;
      reasons.push("fresh");
    } else if (recencyHours <= 24 * 7) {
      score += 2;
      reasons.push("recent");
    } else if (recencyHours <= 24 * 30) {
      score += 1;
      reasons.push("month-ish");
    } else {
      score -= 2;
      reasons.push("stale");
    }
  }
  const maxAgeHours = Number(profile?.timeWindowHours || 0);
  if (maxAgeHours > 0) {
    if (recencyHours === null) {
      score -= 3;
      reasons.push("unknown-age");
    } else if (recencyHours > maxAgeHours) {
      score -= 8;
      reasons.push("outside-time-window");
    } else {
      reasons.push("inside-time-window");
    }
  }

  const isRelevant = score >= 6
    && !reasons.includes("candidate-post")
    && !reasons.includes("outside-time-window")
    && !reasons.includes("wrong-role")
    && !reasons.includes("role-mismatch")
    && !reasons.includes("garbled-text");
  return { score, isRelevant, reasons, recency_hours: recencyHours, time_parse_source: timeMeta.parsedFrom || "" };
}

function summarizeFacebookItems(items, profile) {
  const topAuthors = uniqueStrings(items.map((item) => item.author).filter(Boolean)).slice(0, 5);
  const signals = {
    devops: 0,
    intern: 0,
    location: 0,
    job: 0,
  };
  for (const item of items) {
    const hay = normalizeText(`${item.text || ""}\n${item.excerpt || ""}`);
    if (/devops|sre|cloud|platform|sysadmin/.test(hay)) signals.devops += 1;
    if (/intern|thuc tap|fresher/.test(hay)) signals.intern += 1;
    if (/hcm|tphcm|ho chi minh|sai gon|hcmc/.test(hay)) signals.location += 1;
    if (/tuyen|tuyen dung|viec lam|hiring|recruit/.test(hay)) signals.job += 1;
  }
  const coverage = [];
  if (signals.job) coverage.push(`${signals.job}/${items.length} bài có tín hiệu tuyển dụng`);
  if (signals.intern) coverage.push(`${signals.intern}/${items.length} bài có tín hiệu intern/fresher`);
  if (signals.devops) coverage.push(`${signals.devops}/${items.length} bài có tín hiệu DevOps/cloud`);
  if (signals.location) coverage.push(`${signals.location}/${items.length} bài có tín hiệu HCM`);
  return {
    top_authors: topAuthors,
    coverage,
    intent: profile.original_query,
  };
}

function facebookDomLooksComplete(item, profile) {
  const combined = normalizeText(`${item.text || ""}\n${item.excerpt || ""}`);
  const enoughText = combined.length >= 220;
  const hits =
    (profile.roleTerms.some((term) => combined.includes(normalizeText(term))) ? 1 : 0) +
    (profile.seniorityTerms.some((term) => combined.includes(normalizeText(term))) ? 1 : 0) +
    (profile.locationTerms.some((term) => combined.includes(normalizeText(term))) ? 1 : 0);
  const looksRecruiting = /tuyen|tuyen dung|hiring|recruit|viec lam|career|job/.test(combined);
  return enoughText && (hits >= 2 || looksRecruiting);
}

async function ocrExtractFacebookPost({
  imageBase64,
  query,
  url,
  title,
  excerpt,
}) {
  if (!imageBase64) {
    return { ok: false, text: "", confidence: "low", reason: "empty-image" };
  }
  try {
    const payload = {
      image_base64: imageBase64,
      mime_type: "image/png",
      query: String(query || ""),
      url: String(url || ""),
      title: String(title || ""),
      excerpt: String(excerpt || ""),
    };
    const response = await fetchJson(`${appUrl}/api/host-browser/ocr-extract`, {
      method: "POST",
      headers: { "content-type": "application/json; charset=utf-8" },
      body: JSON.stringify(payload),
    });
    const data = response.data || {};
    return {
      ok: Boolean(response.ok && data.text),
      text: String(data.text || "").trim(),
      confidence: String(data.confidence || "medium"),
      reason: String(data.reason || ""),
    };
  } catch (error) {
    return {
      ok: false,
      text: "",
      confidence: "low",
      reason: error instanceof Error ? error.message : String(error),
    };
  }
}

async function rerankFacebookItems({ query, items, topK = 5 }) {
  if (!query || !Array.isArray(items) || !items.length) {
    return { ok: false, ranked_items: [], criteria: [], summary: "", intent: String(query || "") };
  }
  try {
    const payload = {
      query: String(query || ""),
      top_k: Math.max(1, Math.min(10, Number(topK || 5))),
      items: items.slice(0, 20).map((item) => {
        const cleaned = buildFacebookModelCandidate(item);
        return {
          url: String(cleaned.url || ""),
          author: String(cleaned.author || ""),
          text: String(cleaned.text || ""),
          excerpt: String(cleaned.excerpt || ""),
          time_hint: String(cleaned.time_hint || ""),
          query_used: String(cleaned.query_used || ""),
          score: Number(item.score || 0),
          reasons: Array.isArray(item.reasons) ? item.reasons.slice(0, 8) : [],
        };
      }),
    };
    const response = await fetchJson(`${appUrl}/api/host-browser/facebook-rerank`, {
      method: "POST",
      headers: { "content-type": "application/json; charset=utf-8" },
      body: JSON.stringify(payload),
    });
    const data = response.data || {};
    return {
      ok: Boolean(response.ok && data.ok && Array.isArray(data.ranked_items) && data.ranked_items.length),
      intent: String(data.intent || query || "").trim(),
      criteria: Array.isArray(data.criteria) ? data.criteria.map((item) => String(item || "")).filter(Boolean) : [],
      summary: String(data.summary || "").trim(),
      ranked_items: Array.isArray(data.ranked_items) ? data.ranked_items : [],
    };
  } catch (error) {
    return {
      ok: false,
      intent: String(query || "").trim(),
      criteria: [],
      summary: error instanceof Error ? error.message : String(error),
      ranked_items: [],
    };
  }
}

function shouldKeepFacebookByModel(item = {}, profile = {}) {
  const verdict = String(item.llm_verdict || "").toLowerCase();
  const llmScore = Number(item.llm_score || 0);
  const usableUrl = String(item.url || "").trim();
  const usableRawUrl = String(item.raw_url || "").trim();
  const hasUsableUrl = isUsableFacebookResultUrl(usableUrl) || isUsableFacebookResultUrl(usableRawUrl);
  const cleanedText = buildFacebookModelText(item, 900);
  if (!cleanedText || cleanedText.length < 40) return false;
  if (!hasUsableUrl) return false;
  if (verdict === "weak" || llmScore < 3) return false;
  if (Array.isArray(item.reasons) && (item.reasons.includes("wrong-role") || item.reasons.includes("role-mismatch") || item.reasons.includes("candidate-post"))) {
    return false;
  }
  if (verdict === "medium" && llmScore < 4) return false;
  return true;
}

async function fetchJson(url, init = undefined) {
  let response;
  try {
    const requestInit = init && typeof init === "object" ? { ...init } : {};
    if (!requestInit.signal && typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
      requestInit.signal = AbortSignal.timeout(45000);
    }
    response = await fetch(url, requestInit);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`Fetch failed for ${url}: ${message}`);
  }
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`HTTP ${response.status} from ${url}: ${text.slice(0, 400)}`);
  }
  return response.json();
}

async function getDebugTargets(debugPort = 9222) {
  return fetchJson(`http://127.0.0.1:${debugPort}/json/list`);
}

async function ensureDebugBrowser({ browser = "brave", url = "about:blank", debugPort = 9222, profileDir = "" }) {
  const profilePath = profileDir || path.join(profileRoot, `${browser}-debug-${debugPort}`);
  const lockKey = `${browser}:${debugPort}:${profilePath}`;
  const existing = debugBrowserEnsurePromises.get(lockKey);
  if (existing) return existing;
  const pending = (async () => {
    try {
      const version = await fetchJson(`http://127.0.0.1:${debugPort}/json/version`);
      return { launched: false, version, profile_dir: profilePath };
    } catch {}
    const launched = launchDebugBrowser({ browser, url, debugPort, profileDir: profilePath });
    let version = null;
    let lastError = null;
    for (let attempt = 0; attempt < 20; attempt += 1) {
      await sleep(1000);
      try {
        version = await fetchJson(`http://127.0.0.1:${debugPort}/json/version`);
        break;
      } catch (error) {
        lastError = error;
      }
    }
    if (!version) throw lastError || new Error(`Debug browser did not open on port ${debugPort}`);
    return { launched: true, version, profile_dir: launched.profile_dir };
  })();
  debugBrowserEnsurePromises.set(lockKey, pending);
  try {
    return await pending;
  } finally {
    debugBrowserEnsurePromises.delete(lockKey);
  }
}

async function openDebugTab(debugPort, url) {
  const endpoint = `http://127.0.0.1:${debugPort}/json/new?${encodeURIComponent(url)}`;
  try {
    return await fetchJson(endpoint, { method: "PUT" });
  } catch (error) {
    try {
      return await fetchJson(endpoint);
    } catch {
      const version = await fetchJson(`http://127.0.0.1:${debugPort}/json/version`);
      const browserWs = String(version.webSocketDebuggerUrl || "");
      if (!browserWs) throw error;
      const browserCdp = await connectPageCdp(browserWs);
      try {
        const created = await browserCdp.call("Target.createTarget", { url: String(url || "about:blank") });
        return { id: String(created.targetId || "") };
      } finally {
        try {
          browserCdp.close();
        } catch {}
      }
    }
  }
}

async function closeDebugTarget(debugPort, targetId) {
  if (!targetId) return;
  const endpoint = `http://127.0.0.1:${debugPort}/json/close/${encodeURIComponent(targetId)}`;
  try {
    await fetchJson(endpoint, { method: "GET" });
  } catch {
    // best effort
  }
}

async function listClosableDebugPageTargetIds(debugPort) {
  const targets = await getDebugTargets(debugPort).catch(() => []);
  return (Array.isArray(targets) ? targets : [])
    .filter((item) => {
      if (String(item.type || "") !== "page") return false;
      const url = String(item.url || "");
      const title = String(item.title || "");
      return !url.startsWith("devtools://") && !title.startsWith("DevTools - ");
    })
    .map((item) => String(item.id || ""))
    .filter(Boolean);
}

async function closeAllDebugPageTargets(debugPort, preserveTargetIds = []) {
  const preserve = new Set((Array.isArray(preserveTargetIds) ? preserveTargetIds : []).map((item) => String(item || "")).filter(Boolean));
  const targetIds = await listClosableDebugPageTargetIds(debugPort);
  for (const targetId of targetIds) {
    if (preserve.has(targetId)) continue;
    await closeDebugTarget(debugPort, targetId);
  }
}

async function trimDebugPageTargetsButKeepOne(debugPort) {
  const targetIds = await listClosableDebugPageTargetIds(debugPort);
  if (targetIds.length <= 1) return { keptTargetId: String(targetIds[0] || "") };
  const [keptTargetId, ...rest] = targetIds;
  for (const targetId of rest) {
    await closeDebugTarget(debugPort, targetId);
  }
  return { keptTargetId: String(keptTargetId || "") };
}

async function activateDebugTarget(debugPort, targetId) {
  if (!targetId) return;
  const endpoint = `http://127.0.0.1:${debugPort}/json/activate/${encodeURIComponent(targetId)}`;
  try {
    await fetchJson(endpoint, { method: "GET" });
  } catch {
    // best effort
  }
}

async function connectPageCdp(webSocketDebuggerUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(webSocketDebuggerUrl);
    const pending = new Map();
    let nextId = 1;
    const call = (method, params = {}) =>
      new Promise((resolveCall, rejectCall) => {
        const id = nextId++;
        pending.set(id, { resolve: resolveCall, reject: rejectCall });
        ws.send(JSON.stringify({ id, method, params }));
      });
    ws.addEventListener("open", () => {
      resolve({
        call,
        close: () => ws.close(),
      });
    });
    ws.addEventListener("message", (event) => {
      try {
        const message = JSON.parse(String(event.data || ""));
        if (!message.id) return;
        const item = pending.get(message.id);
        if (!item) return;
        pending.delete(message.id);
        if (message.error) {
          item.reject(new Error(message.error.message || JSON.stringify(message.error)));
        } else {
          item.resolve(message.result || {});
        }
      } catch (error) {
        reject(error);
      }
    });
    ws.addEventListener("error", (error) => reject(error));
    ws.addEventListener("close", () => {
      for (const [, item] of pending.entries()) {
        item.reject(new Error("CDP socket closed"));
      }
      pending.clear();
    });
  });
}

async function openResearchTab(debugPort, initialUrl) {
  const targets = await getDebugTargets(debugPort);
  const pages = Array.isArray(targets) ? targets.filter((item) => item.type === "page") : [];
  const matches = pages.filter((item) => {
    const url = String(item.url || "");
    const title = String(item.title || "");
    if (url.startsWith("devtools://") || title.startsWith("DevTools - ")) {
      return false;
    }
    return (
      url.includes("facebook.com/search/posts") ||
      url.includes("facebook.com/search/") ||
      title.includes("Search Results | Facebook")
    );
  });
  const facebookPages = pages.filter((item) => {
    const url = String(item.url || "");
    const title = String(item.title || "");
    if (url.startsWith("devtools://") || title.startsWith("DevTools - ")) return false;
    return url.includes("facebook.com");
  });
  const reusable = [...matches, ...facebookPages].find((item) => item.webSocketDebuggerUrl);
  if (reusable) {
    const page = await connectPageCdp(reusable.webSocketDebuggerUrl);
    try {
      await activateDebugTarget(debugPort, reusable.id);
      await page.call("Page.bringToFront");
      if (initialUrl && String(reusable.url || "") !== String(initialUrl)) {
        await page.call("Page.navigate", { url: String(initialUrl) });
      }
    } catch {
      // best effort
    }
    return { targetId: reusable.id, page, reused: true };
  }
  const created = await openDebugTab(debugPort, initialUrl);
  await activateDebugTarget(debugPort, created.id);
  await sleep(1200);
  const refreshedTargets = await getDebugTargets(debugPort);
  const refreshed = (Array.isArray(refreshedTargets) ? refreshedTargets : []).find((item) => item.id === created.id) || created;
  if (!refreshed.webSocketDebuggerUrl) {
    throw new Error("No websocket debugger URL returned for research tab");
  }
  const page = await connectPageCdp(refreshed.webSocketDebuggerUrl);
  try {
    await page.call("Page.bringToFront");
  } catch {
    // best effort
  }
  return { targetId: refreshed.id, page, reused: false };
}

async function openFreshResearchTab(debugPort, initialUrl) {
  const created = await openDebugTab(debugPort, initialUrl);
  await sleep(1200);
  const refreshedTargets = await getDebugTargets(debugPort);
  const refreshed = (Array.isArray(refreshedTargets) ? refreshedTargets : []).find((item) => item.id === created.id) || created;
  if (!refreshed.webSocketDebuggerUrl) {
    throw new Error("No websocket debugger URL returned for fresh research tab");
  }
  const page = await connectPageCdp(refreshed.webSocketDebuggerUrl);
  return { targetId: refreshed.id, page, reused: false };
}

async function connectExistingResearchTab(debugPort, targetId) {
  const targets = await getDebugTargets(debugPort);
  const target = (Array.isArray(targets) ? targets : []).find((item) => String(item.id || "") === String(targetId || "") && String(item.type || "") === "page");
  if (!target?.webSocketDebuggerUrl) {
    throw new Error(`Research tab not found for target ${targetId}`);
  }
  const page = await connectPageCdp(String(target.webSocketDebuggerUrl));
  try {
    await page.call("Page.enable");
  } catch {}
  try {
    await page.call("Page.bringToFront");
  } catch {}
  return { target, page };
}

async function mapWithConcurrency(items, limit, iteratee) {
  const list = Array.isArray(items) ? items : [];
  const maxConcurrency = Math.max(1, Math.min(Number(limit || 1), list.length || 1));
  const results = new Array(list.length);
  let cursor = 0;
  const workers = Array.from({ length: maxConcurrency }, async () => {
    while (true) {
      const currentIndex = cursor;
      cursor += 1;
      if (currentIndex >= list.length) return;
      results[currentIndex] = await iteratee(list[currentIndex], currentIndex);
    }
  });
  await Promise.all(workers);
  return results;
}

async function applyFacebookSearchUiFilters(page, timeWindow = null) {
  if (!timeWindow) return { ok: true, recentPosts: false, years: [] };
  const targetYears = deriveFacebookFilterYears(timeWindow);
  const openResult = await page.call("Runtime.evaluate", {
    expression: `
((years) => {
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const targetYears = Array.isArray(years) ? years.map((item) => String(item)) : [];
  let openedAll = false;
  let openedDatePosted = false;
  let recentPosts = false;
  const region =
    document.querySelector('[aria-label="Result filters"]') ||
    document.querySelector('[role="region"][aria-label="Result filters"]') ||
    [...document.querySelectorAll('[role="region"], div, section, aside')].find((el) => {
      const text = normalize(el.innerText || '');
      const rect = el.getBoundingClientRect();
      return rect.width >= 180 && rect.height >= 220 && rect.left < window.innerWidth * 0.35
        && text.includes('filters') && text.includes('all');
    }) ||
    document.body;
  const isVisible = (el) => {
    const rect = el.getBoundingClientRect();
    return rect.width >= 16 && rect.height >= 10 && rect.bottom >= 0 && rect.top <= window.innerHeight;
  };
  const clickLikeUser = (el) => {
    if (!el) return false;
    try {
      el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, pointerType: 'mouse', isPrimary: true, button: 0, buttons: 1 }));
      el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0, buttons: 1 }));
      el.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true, pointerType: 'mouse', isPrimary: true, button: 0, buttons: 0 }));
      el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, button: 0, buttons: 0 }));
      el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, buttons: 0 }));
      if (typeof el.click === 'function') el.click();
      return true;
    } catch {
      try {
        if (typeof el.click === 'function') {
          el.click();
          return true;
        }
      } catch {}
    }
    return false;
  };
  const getScreenPoint = (el) => {
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    if (!rect || rect.width < 1 || rect.height < 1) return null;
    const viewportLeft = Math.max(0, (window.outerWidth - window.innerWidth) / 2);
    const viewportTop = Math.max(0, window.outerHeight - window.innerHeight);
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
      screenX: (window.screenX || window.screenLeft || 0) + viewportLeft + rect.left + rect.width / 2,
      screenY: (window.screenY || window.screenTop || 0) + viewportTop + rect.top + rect.height / 2,
    };
  };
  const listYearLabels = () => [...document.querySelectorAll('[role="option"], [role="listbox"] [tabindex], [role="menuitemradio"], [role="menuitem"], li, div, span')]
    .filter((el) => isVisible(el))
    .map((el) => String(el.textContent || el.innerText || '').trim())
    .filter((text, index, arr) => /^20\\d{2}$/.test(text) && arr.indexOf(text) === index)
    .slice(0, 20);
  const getNodes = () => [...region.querySelectorAll('div[role="button"], span[role="button"], a[role="link"], label, span, div, li, [tabindex]')]
    .filter((el) => isVisible(el));
  const clickHost = (el) => el?.closest?.('div[role="button"], span[role="button"], a[role="link"], li, [tabindex]') || el;
  let allButtons = getNodes();
  const allLabel = allButtons.find((el) => /^(all|tất cả)$/i.test(String((el.textContent || el.innerText || '')).trim()));
  const allTarget = clickHost(allLabel);
  if (allTarget) {
    try {
      clickLikeUser(allTarget);
      openedAll = true;
    } catch {}
  }
  allButtons = getNodes();
  const datePosted = allButtons.find((el) => /date posted|ngày đăng/i.test(String((el.textContent || el.innerText || '')).trim()));
  const dateTarget = clickHost(datePosted);
  const filterRow = datePosted?.closest?.('label, li, div[role="button"], div, section, article') || dateTarget?.parentElement || null;
  const comboboxNode =
    filterRow?.querySelector?.('[role="combobox"]') ||
    dateTarget?.querySelector?.('[role="combobox"]') ||
    datePosted?.querySelector?.('[role="combobox"]') ||
    [...document.querySelectorAll('[role="combobox"]')].find((el) => {
      if (!isVisible(el)) return false;
      const text = normalize(el.innerText || el.textContent || '');
      const label = normalize(el.getAttribute?.('aria-label') || '');
      const rowText = normalize(filterRow?.innerText || '');
      return text.includes('year') || label.includes('year') || rowText.includes('date posted');
    }) ||
    null;
  const comboboxTarget = clickHost(comboboxNode);
  const isExpanded = () => {
    const comboExpanded = normalize(comboboxTarget?.getAttribute?.('aria-expanded') || comboboxNode?.getAttribute?.('aria-expanded') || '');
    return comboExpanded === 'true' || listYearLabels().length > 0 || !!document.querySelector('[role="listbox"], [role="menu"]');
  };
  if (comboboxTarget) {
    try {
      clickLikeUser(comboboxTarget);
      openedDatePosted = true;
    } catch {}
  }
  if (!isExpanded() && dateTarget) {
    try {
      clickLikeUser(dateTarget);
      openedDatePosted = true;
    } catch {}
  }
  return {
    ok: true,
    openedAll,
    openedDatePosted,
    recentPosts,
    targetYears,
    expanded: isExpanded(),
    yearsVisible: listYearLabels(),
    dateClickPoint: getScreenPoint(dateTarget || datePosted || comboboxTarget),
    comboboxClickPoint: getScreenPoint(comboboxTarget || comboboxNode),
    regionLabel: region.getAttribute?.('aria-label') || '',
    regionText: String(region.innerText || '').slice(0, 400),
  };
})(${JSON.stringify(targetYears)})
    `,
    returnByValue: true,
    awaitPromise: true,
  });
  let openState = openResult.result?.value || { ok: true, openedAll: false, openedDatePosted: false, recentPosts: false, yearsVisible: [] };
  await sleep(900);
  if (!openState.expanded && (openState.comboboxClickPoint?.x || openState.dateClickPoint?.x)) {
    const fallbackPoint = openState.comboboxClickPoint?.x ? openState.comboboxClickPoint : openState.dateClickPoint;
    await cdpClickAt(page, fallbackPoint.x, fallbackPoint.y);
    openState = { ...openState, openedDatePosted: true };
    await sleep(1100);
  }
  const selectResult = await page.call("Runtime.evaluate", {
    expression: `
((years) => {
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const targetYears = Array.isArray(years) ? years.map((item) => String(item)) : [];
  const isVisible = (el) => {
    const rect = el.getBoundingClientRect();
    return rect.width >= 16 && rect.height >= 10 && rect.bottom >= 0 && rect.top <= window.innerHeight;
  };
  const clickLikeUser = (el) => {
    if (!el) return false;
    try {
      el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, pointerType: 'mouse', isPrimary: true, button: 0, buttons: 1 }));
      el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0, buttons: 1 }));
      el.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true, pointerType: 'mouse', isPrimary: true, button: 0, buttons: 0 }));
      el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, button: 0, buttons: 0 }));
      el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, buttons: 0 }));
      if (typeof el.click === 'function') el.click();
      return true;
    } catch {
      try {
        if (typeof el.click === 'function') {
          el.click();
          return true;
        }
      } catch {}
    }
    return false;
  };
  const optionNodes = [...document.querySelectorAll('[role="option"], [role="menuitemradio"], [role="menuitem"], [role="listbox"] [tabindex], li, div, span')]
    .filter((el) => isVisible(el));
  const visibleYears = optionNodes
    .map((el) => String(el.textContent || el.innerText || '').trim())
    .filter((text, index, arr) => /^20\\d{2}$/.test(text) && arr.indexOf(text) === index)
    .slice(0, 20);
  const clickedYears = [];
  for (const year of targetYears) {
    const yearNode = optionNodes.find((el) => String((el.textContent || el.innerText || '')).trim() === year);
    if (!yearNode) continue;
    const yearTarget = yearNode.closest?.('div[role="button"], span[role="button"], a[role="link"], [role="option"], [role="menuitemradio"], [role="menuitem"], li, [tabindex]') || yearNode;
    const selected = normalize(yearNode.getAttribute?.('aria-pressed') || '') === 'true'
      || normalize(yearNode.getAttribute?.('aria-current') || '') === 'true'
      || normalize(yearNode.getAttribute?.('aria-selected') || '') === 'true';
    if (!selected && clickLikeUser(yearTarget)) {
      clickedYears.push(year);
    } else if (selected) {
      clickedYears.push(year);
    }
  }
  return {
    ok: true,
    yearsVisible: visibleYears,
    years: clickedYears,
  };
})(${JSON.stringify(targetYears)})
    `,
    returnByValue: true,
    awaitPromise: true,
  });
  await sleep(1400);
  const selectState = selectResult.result?.value || { ok: true, yearsVisible: [], years: [] };
  return {
    ok: true,
    openedAll: Boolean(openState.openedAll),
    openedDatePosted: Boolean(openState.openedDatePosted),
    recentPosts: Boolean(openState.recentPosts),
    years: Array.isArray(selectState.years) ? selectState.years : [],
    yearsVisible: Array.isArray(selectState.yearsVisible) ? selectState.yearsVisible : (Array.isArray(openState.yearsVisible) ? openState.yearsVisible : []),
    targetYears,
    regionLabel: openState.regionLabel || '',
    regionText: openState.regionText || '',
  };
}

async function readFacebookFilterUiState(page, targetYears = []) {
  const result = await page.call("Runtime.evaluate", {
    expression: `
((targetYears) => {
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const region =
    document.querySelector('[aria-label="Result filters"]') ||
    document.querySelector('[role="region"][aria-label="Result filters"]') ||
    null;
  const regionText = normalize(region?.innerText || '');
  const resetVisible = /\\breset\\b/i.test(regionText);
  const nodeTexts = region
    ? [...region.querySelectorAll('div, span, a, li, label')]
        .map((el) => normalize(el.textContent || el.innerText || ''))
        .filter(Boolean)
    : [];
  const yearsVisible = (Array.isArray(targetYears) ? targetYears : [])
    .map((year) => String(year))
    .filter((year) => regionText.includes(year) || nodeTexts.includes(year));
  return {
    regionFound: Boolean(region),
    regionText: regionText.slice(0, 500),
    resetVisible,
    yearsVisible,
    uiFilterApplied: Boolean(region && resetVisible && yearsVisible.length > 0),
  };
})(${JSON.stringify(targetYears)})
    `,
    returnByValue: true,
    awaitPromise: true,
  });
  return result.result?.value || {
    regionFound: false,
    regionText: "",
    resetVisible: false,
    yearsVisible: [],
    uiFilterApplied: false,
  };
}

async function processFacebookSearchQuery({
  searchQuery,
  queryIndex = 0,
  browser,
  debugPort,
  limit,
  scanCandidateQuota = 12,
  scrollRounds,
  plan,
  timeWindow,
}) {
  const fastScanMode = !["0", "false", "off", "no"].includes(String(process.env.FACEBOOK_FAST_SCAN || "1").trim().toLowerCase());
  const timing = {
    started_at: Date.now(),
    navigate_started_at: 0,
    first_scroll_started_at: 0,
    first_scroll_done_at: 0,
    settle_done_at: 0,
    extraction_started_at: 0,
    extraction_done_at: 0,
  };
  const targetUrl = String(searchQuery || "").startsWith("http")
    ? String(searchQuery)
    : buildFacebookSearchUrl(searchQuery, timeWindow);
  const targetYears = timeWindow ? deriveFacebookFilterYears(timeWindow) : [];
  const researchTab = await openFreshResearchTab(debugPort, targetUrl);
  const page = researchTab.page;
  const collected = [];
  const resolvedInPlace = [];
  const candidateCap = Math.max(8, Math.min(18, Number(scanCandidateQuota || 12)));
  let lastTitle = "";
  let lastUrl = "";
  let needsLogin = false;
  try {
    await page.call("Page.enable");
    timing.navigate_started_at = Date.now();
    await page.call("Page.navigate", { url: targetUrl });
    if (fastScanMode && !targetYears.length) {
      timing.first_scroll_started_at = Date.now();
      await sleep(300);
      await eagerFacebookScrollBurst(page, Math.min(2, Math.max(1, scrollRounds)));
      timing.first_scroll_done_at = Date.now();
    }
    await waitForFacebookSearchSettled(page, {
      minWaitMs: fastScanMode ? 700 : 4500,
      maxWaitMs: fastScanMode ? 4000 : 26000,
      pollMs: fastScanMode ? 350 : 1400,
      stableRounds: fastScanMode ? 0 : 2,
      scroll: false,
    });
    timing.settle_done_at = Date.now();
    const afterNavigateProgress = await readFacebookSearchProgress(page);
    let uiFilterState = await readFacebookFilterUiState(page, targetYears);
    let filterRetryCount = 0;
    const hasTargetYears = Array.isArray(targetYears) && targetYears.length > 0;
    const hrefHasAllTargetYears = (href = "") => !hasTargetYears || facebookHrefHasCreationTimeFilter(href, targetYears);
    let filterState = {
      ok: true,
      openedAll: false,
      openedDatePosted: false,
      recentPosts: false,
      years: hrefHasAllTargetYears(afterNavigateProgress.href || "") ? [...targetYears] : [],
      yearsVisible: Array.isArray(uiFilterState?.yearsVisible) && uiFilterState.yearsVisible.length ? uiFilterState.yearsVisible : (hrefHasAllTargetYears(afterNavigateProgress.href || "") ? [...targetYears] : []),
      targetYears,
      regionLabel: uiFilterState.regionFound ? "Result filters" : "",
      regionText: String(uiFilterState.regionText || ""),
      filter_retry_count: 0,
      filter_source: "url",
      ui_filter_applied: Boolean(uiFilterState.uiFilterApplied),
      filter_applied: Boolean(uiFilterState.uiFilterApplied || (String(afterNavigateProgress.href || "").includes("filters=") && hrefHasAllTargetYears(afterNavigateProgress.href || ""))),
    };
    while (hasTargetYears && !filterState.filter_applied && filterRetryCount < 2) {
      filterRetryCount += 1;
      await sleep(fastScanMode ? 300 : 800);
      const uiState = await applyFacebookSearchUiFilters(page, timeWindow);
      uiFilterState = await readFacebookFilterUiState(page, targetYears);
      filterState = {
        ...uiState,
        targetYears,
        filter_retry_count: filterRetryCount,
        yearsVisible: Array.isArray(uiFilterState?.yearsVisible) && uiFilterState.yearsVisible.length ? uiFilterState.yearsVisible : (Array.isArray(uiState?.yearsVisible) ? uiState.yearsVisible : []),
        regionLabel: uiFilterState.regionFound ? "Result filters" : (uiState.regionLabel || ""),
        regionText: String(uiFilterState.regionText || uiState.regionText || ""),
        filter_source: "ui_fallback",
        ui_filter_applied: Boolean(uiFilterState.uiFilterApplied),
        filter_applied: Boolean(uiFilterState.uiFilterApplied || targetYears.every((year) => Array.isArray(uiState?.years) && uiState.years.includes(year))),
      };
    }
    if (filterState?.filter_applied || (Array.isArray(filterState?.years) && filterState.years.length)) {
      await waitForFacebookSearchSettled(page, {
        minWaitMs: fastScanMode ? 900 : 1800,
        maxWaitMs: fastScanMode ? 5000 : 12000,
        pollMs: fastScanMode ? 600 : 900,
        stableRounds: 1,
        scroll: false,
      });
    }
    const initialProgress = await readFacebookSearchProgress(page);
    const visibleStoryCount = Number(initialProgress.storyMessageCount || 0);
    const shouldPreScroll = fastScanMode || visibleStoryCount < Math.max(Math.min(candidateCap, 12), 6);
    if (shouldPreScroll) {
      for (let i = 0; i < scrollRounds; i += 1) {
        if (fastScanMode) {
          await eagerFacebookScrollBurst(page, 1);
          await sleep(180);
        } else {
          await waitForFacebookSearchSettled(page, {
            minWaitMs: 1200,
            maxWaitMs: 9000,
            pollMs: 1200,
            stableRounds: 2,
            scroll: true,
          });
        }
      }
    }
    await sleep(fastScanMode ? 250 : 1200);
    const debugProgress = await readFacebookSearchProgress(page);
    const sourceStateEval = await page.call("Runtime.evaluate", {
      expression: "({ href: location.href, scrollY: window.scrollY || window.pageYOffset || 0 })",
      returnByValue: true,
      awaitPromise: true,
    });
    const sourceState = sourceStateEval.result?.value || { href: targetUrl, scrollY: 0 };
    timing.extraction_started_at = Date.now();
    const evalResult = await page.call("Runtime.evaluate", {
      expression: `
(() => {
  const loginText = (document.body?.innerText || '').toLowerCase();
  const needsLogin = location.href.includes('login') || loginText.includes('log in') || loginText.includes('đăng nhập');
  const storyNodes = [...document.querySelectorAll('[data-ad-rendering-role="story_message"], div[data-ad-preview="message"], div[data-ad-comet-preview="message"]')];
  const items = [];
  const seen = new Set();
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const boilerplatePatterns = [
    /^facebook(?:\\s+facebook)+$/i,
    /^(like|comment|share|send|reply|follow|joined?|join group|see more|view more)$/i,
    /^(thích|bình luận|chia sẻ|gửi|trả lời|theo dõi|tham gia|xem thêm)$/i,
    /^\\d+\\s*(likes?|comments?|shares?)$/i,
    /^\\d+\\s*(lượt thích|bình luận|chia sẻ)$/i,
    /^(public group|private group|group by .*)$/i,
  ];
  const looksBoilerplate = (line) => {
    const value = normalize(line);
    if (!value) return true;
    if (value.length <= 2) return true;
    if (boilerplatePatterns.some((pattern) => pattern.test(value))) return true;
    if (/^facebook\\b/i.test(value) && value.split(' ').length <= 6) return true;
    if (/^(home|menu|reels|friends|marketplace|watch)$/i.test(value)) return true;
    return false;
  };
  const extractMeaningfulPostText = (node, authorText = '') => {
    const candidates = [];
    const storyRoot =
      node.querySelector('[data-ad-rendering-role="story_message"]') ||
      node.querySelector('div[data-ad-preview="message"]') ||
      node.querySelector('div[data-ad-comet-preview="message"]');
    const selector = [
      '[data-ad-rendering-role="story_message"] span[dir="auto"]',
      '[data-ad-rendering-role="story_message"] div[dir="auto"]',
      '[data-ad-rendering-role="story_message"] h4',
      'div[data-ad-preview="message"] span[dir="auto"]',
      'div[data-ad-preview="message"] div[dir="auto"]',
      'div[data-ad-comet-preview="message"] span[dir="auto"]',
      'div[data-ad-comet-preview="message"] div[dir="auto"]',
    ].join(', ');
    const seenLines = new Set();
    const authorNorm = normalize(authorText).toLowerCase();
    const pushLine = (raw) => {
      const line = normalize(raw);
      const key = line.toLowerCase();
      if (!line || seenLines.has(key) || looksBoilerplate(line)) return;
      if (authorNorm && key === authorNorm) return;
      seenLines.add(key);
      candidates.push(line);
    };
    const preferredRoot = storyRoot || node;
    [...preferredRoot.querySelectorAll(selector)].forEach((el) => {
      const text = normalize(el.textContent || el.innerText || '');
      if (!text) return;
      text.split(/\\n+/).forEach(pushLine);
    });
    if (!candidates.length && storyRoot) {
      [...storyRoot.querySelectorAll('span[dir="auto"], div[dir="auto"], h4, h3, h5')].forEach((el) => {
        const text = normalize(el.textContent || el.innerText || '');
        if (!text) return;
        text.split(/\\n+/).forEach(pushLine);
      });
    }
    if (!candidates.length) {
      [...node.querySelectorAll('span[dir="auto"], div[dir="auto"]')].forEach((el) => {
        const text = normalize(el.textContent || el.innerText || '');
        if (!text) return;
        text.split(/\\n+/).forEach(pushLine);
      });
    }
    if (!candidates.length) {
      normalize(node.innerText || '').split(/\\n+/).forEach(pushLine);
    }
    const joined = candidates
      .filter((line) => line.length >= 12)
      .join(' ')
      .replace(/\\bFacebook(?:\\s+Facebook)+\\b/gi, 'Facebook')
      .trim();
    return joined;
  };
  const isSearchWrapperHref = (href) => {
    const value = String(href || '');
    if (!value) return true;
    if (
      value.includes('/permalink/') ||
      value.includes('/posts/') ||
      value.includes('story_fbid=') ||
      value.includes('/share/p/') ||
      value.includes('permalink.php?')
    ) {
      return false;
    }
    return value.includes('/search/posts/') || value.includes('/search/top') || value.includes('__cft__') || value.includes('__tn__=');
  };
  const isLikelyPostHref = (href) => {
    const value = String(href || '');
    if (!value) return false;
    return (
      value.includes('/posts/') ||
      value.includes('/permalink/') ||
      value.includes('story_fbid=') ||
      (value.includes('/groups/') && value.includes('/posts/')) ||
      value.includes('/share/p/') ||
      value.includes('permalink.php?')
    );
  };
  const isResolvableFacebookHref = (href) => {
    const value = String(href || '');
    if (!value || !/facebook\\.com/i.test(value)) return false;
    if (isLikelyPostHref(value)) return true;
    if (value.includes('/search/posts/') || value.includes('/search/top')) return true;
    if (value.includes('__cft__') || value.includes('__tn__=')) return true;
    if (value.includes('/groups/')) return true;
    if (value.includes('profile.php?id=')) return true;
    return false;
  };
  const linkSelector = [
    'a[href*="/posts/"]',
    'a[href*="/permalink/"]',
    'a[href*="story_fbid="]',
    'a[href*="/groups/"][href*="/posts/"]',
    'a[href*="permalink.php?"]',
    'a[href*="/search/posts/"][href*="__cft__"]',
    'a[href*="/search/top"][href*="__cft__"]',
    'a[href*="/groups/"][href*="__cft__"]',
    'a[href*="profile.php?id="][href*="__cft__"]',
    'a[href*="__cft__"][href*="__tn__="]',
  ].join(', ');
  const containers = [];
  if (storyNodes.length) {
    const findStoryContext = (start) => {
      let current = start;
      let hops = 0;
      let best = start.parentElement || start;
      while (current && hops < 8) {
        const text = normalize(current.innerText || '');
        const links = current.querySelectorAll ? current.querySelectorAll('a[href]').length : 0;
        if (text.length >= 80 && text.length <= 6000) {
          best = current;
        }
        if (links >= 3) {
          return current;
        }
        current = current.parentElement;
        hops += 1;
      }
      return best;
    };
    storyNodes.forEach((node, idx) => {
      containers.push({ node: findStoryContext(node), storyNode: node, idx });
    });
  } else {
    const links = [...document.querySelectorAll(linkSelector)];
    const findContainer = (start) => {
      let current = start;
      let hops = 0;
      let best = start.parentElement || start;
      let bestLen = 0;
      while (current && hops < 8) {
        const text = (current.innerText || '').replace(/\\s+/g, ' ').trim();
        if (text.length >= 80 && text.length <= 5000 && text.length >= bestLen) {
          best = current;
          bestLen = text.length;
        }
        current = current.parentElement;
        hops += 1;
      }
      return best;
    };
    links.forEach((link, idx) => {
      containers.push({ node: findContainer(link), idx });
    });
  }
  for (const { node, storyNode, idx } of containers) {
    const anchors = [...node.querySelectorAll('a[href]')];
    const anchorScore = (anchor) => {
      const href = String(anchor.href || '');
      const text = normalize(anchor.textContent || anchor.innerText || anchor.getAttribute('aria-label') || '');
      let score = 0;
      if (isLikelyPostHref(href)) score += 20;
      else if (isResolvableFacebookHref(href)) score += 10;
      if (anchor.querySelector && (anchor.querySelector('abbr') || anchor.querySelector('time'))) score += 8;
      if (/^\\d+\\s*(phut|ph|mins|min|gio|hour|hours|ngay|day|days|tuan|week|weeks|thang|month|months)$/i.test(text)) score += 8;
      if (/^(yesterday|today|hom qua|hom nay)$/i.test(text)) score += 6;
      if (/see more|xem them|like|comment|share|thich|binh luan|chia se|join/i.test(text)) score -= 8;
      if (/\\/search\\/(people|groups|pages|videos|marketplace)/i.test(href)) score -= 10;
      return score;
    };
    const rankedLinks = anchors
      .filter((anchor) => isResolvableFacebookHref(anchor.href))
      .map((anchor) => ({ anchor, score: anchorScore(anchor) }))
      .sort((left, right) => right.score - left.score);
    const directPostLink = rankedLinks.find((item) => isLikelyPostHref(item.anchor.href))?.anchor || null;
    const bestResolvableLink = rankedLinks.find((item) => item.score >= 2)?.anchor || null;
    const href = directPostLink ? directPostLink.href : '';
    const raw_href = bestResolvableLink ? bestResolvableLink.href : '';
    const text = (node.innerText || '').replace(/\\s+/g, ' ').trim();
    const authorLink = [...anchors].find((anchor) => {
      const value = normalize(anchor.textContent || anchor.innerText || '');
      if (!value || value.length < 2) return false;
      if (looksBoilerplate(value)) return false;
      if (/see more|xem them|like|comment|share|thich|binh luan|chia se/i.test(value)) return false;
      return true;
    }) || null;
    const authorText = authorLink ? normalize(authorLink.textContent || authorLink.innerText || '') : '';
    const time =
      node.querySelector('a[aria-label][role="link"]') ||
      node.querySelector('span[aria-label]') ||
      node.querySelector('a[href*="/posts/"] span, a[href*="story_fbid="] span, a[href*="/search/posts/"][href*="__cft__"] span, a[href*="permalink.php?"] span, a[href*="profile.php?id="][href*="__cft__"] span');
    const rect = node.getBoundingClientRect();
    const storyRect = (storyNode || node).getBoundingClientRect();
    const mediaCandidates = [
      ...node.querySelectorAll('img'),
      ...node.querySelectorAll('[role="img"]'),
    ];
    let bestMedia = null;
    let bestMediaArea = 0;
    for (const media of mediaCandidates) {
      const mediaRect = media.getBoundingClientRect();
      const area = Math.max(0, mediaRect.width) * Math.max(0, mediaRect.height);
      if (mediaRect.width < 120 || mediaRect.height < 120) continue;
      if (area <= bestMediaArea) continue;
      bestMedia = mediaRect;
      bestMediaArea = area;
    }
    const meaningfulText = extractMeaningfulPostText(storyNode || node, authorText);
    if ((!href && !raw_href && !authorText) || (!text && !meaningfulText)) continue;
    const excerpt = meaningfulText ? meaningfulText.slice(0, 320) : text.slice(0, 320);
    const dedupeKey = href || raw_href || (authorText + '::' + excerpt.slice(0, 120));
    if (seen.has(dedupeKey)) continue;
    seen.add(dedupeKey);
    items.push({
      dom_index: idx,
      url: href,
      raw_url: raw_href,
      author: authorText,
      text: (meaningfulText || text).slice(0, 1800),
      excerpt,
      time_hint: time ? ((time.getAttribute && time.getAttribute('aria-label')) || time.textContent || '').trim() : '',
      dom_chars: text.length,
      bounds: {
        x: Math.max(0, rect.left + window.scrollX),
        y: Math.max(0, rect.top + window.scrollY),
        width: Math.max(0, rect.width),
        height: Math.max(0, rect.height),
      },
      story_bounds: {
        x: Math.max(0, storyRect.left + window.scrollX - 12),
        y: Math.max(0, storyRect.top + window.scrollY - 12),
        width: Math.max(280, storyRect.width + 24),
        height: Math.max(160, storyRect.height + 24),
      },
      image_bounds: bestMedia ? {
        x: Math.max(0, bestMedia.left + window.scrollX - 8),
        y: Math.max(0, bestMedia.top + window.scrollY - 8),
        width: Math.max(160, bestMedia.width + 16),
        height: Math.max(160, bestMedia.height + 16),
      } : null,
    });
  }
  return {
    title: document.title,
    url: location.href,
    needs_login: needsLogin,
    total_found: items.length,
    items: items.slice(0, ${candidateCap})
  };
})()
      `,
      returnByValue: true,
      awaitPromise: true,
    });
    timing.extraction_done_at = Date.now();
    const value = evalResult.result?.value || {};
    lastTitle = String(value.title || "");
    lastUrl = String(value.url || targetUrl);
    needsLogin = needsLogin || Boolean(value.needs_login);
    let rawItems = Array.isArray(value.items) ? value.items : [];
    let fallbackCount = 0;
    if (!rawItems.length) {
      const fallbackEval = await page.call("Runtime.evaluate", {
        expression: `
(() => {
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const nodes = [...document.querySelectorAll('[data-ad-rendering-role="story_message"], div[data-ad-preview="message"], div[data-ad-comet-preview="message"]')];
  const unique = [];
  const seen = new Set();
  for (const node of nodes) {
    const text = normalize(node.innerText || '');
    const key = text.slice(0, 180).toLowerCase();
    if (!text || seen.has(key)) continue;
    seen.add(key);
    unique.push(node);
  }
  const items = [];
  for (const [index, story] of unique.slice(0, ${candidateCap}).entries()) {
    let current = story;
    let context = story;
    let hops = 0;
    while (current && hops < 8) {
      const links = current.querySelectorAll ? current.querySelectorAll('a[href]').length : 0;
      const text = normalize(current.innerText || '');
      if (links >= 3 && text.length > 60) { context = current; break; }
      current = current.parentElement;
      hops += 1;
    }
    const anchors = [...context.querySelectorAll('a[href]')];
    const authorAnchor = anchors.find((a) => {
      const text = normalize(a.textContent || a.innerText || '');
      return text && text.length >= 2 && !/^(see more|xem thêm|share|like|comment|join)$/i.test(text);
    });
    const rawAnchor = anchors
      .map((anchor) => {
        const href = String(anchor.href || '');
        const text = normalize(anchor.textContent || anchor.innerText || anchor.getAttribute?.('aria-label') || '');
        let score = 0;
        if (href.includes('/permalink/') || href.includes('/posts/') || href.includes('story_fbid=') || href.includes('permalink.php?')) score += 20;
        else if (href.includes('/search/posts/') || href.includes('/search/top') || href.includes('__cft__') || href.includes('/groups/') || href.includes('profile.php?id=')) score += 10;
        if (/see more|xem them|like|comment|share|join/i.test(text)) score -= 8;
        return { anchor, score };
      })
      .filter((item) => item.score >= 2)
      .sort((left, right) => right.score - left.score)[0]?.anchor || null;
    const storyRect = story.getBoundingClientRect();
    const media = [...context.querySelectorAll('img,[role="img"]')].map((el) => el.getBoundingClientRect()).filter((rect) => rect.width >= 120 && rect.height >= 120).sort((a, b) => (b.width * b.height) - (a.width * a.height))[0] || null;
    items.push({
      dom_index: index,
      url: '',
      raw_url: rawAnchor ? rawAnchor.href : '',
      author: authorAnchor ? normalize(authorAnchor.textContent || authorAnchor.innerText || authorAnchor.getAttribute('aria-label') || '') : '',
      text: normalize(story.innerText || '').slice(0, 1800),
      excerpt: normalize(story.innerText || '').slice(0, 320),
      time_hint: '',
      dom_chars: normalize(story.innerText || '').length,
      bounds: {
        x: Math.max(0, storyRect.left + window.scrollX - 12),
        y: Math.max(0, storyRect.top + window.scrollY - 12),
        width: Math.max(280, storyRect.width + 24),
        height: Math.max(160, storyRect.height + 24),
      },
      story_bounds: {
        x: Math.max(0, storyRect.left + window.scrollX - 12),
        y: Math.max(0, storyRect.top + window.scrollY - 12),
        width: Math.max(280, storyRect.width + 24),
        height: Math.max(160, storyRect.height + 24),
      },
      image_bounds: media ? {
        x: Math.max(0, media.left + window.scrollX - 8),
        y: Math.max(0, media.top + window.scrollY - 8),
        width: Math.max(160, media.width + 16),
        height: Math.max(160, media.height + 16),
      } : null,
    });
  }
  return { items, title: document.title, url: location.href };
})()
        `,
        returnByValue: true,
        awaitPromise: true,
      });
      const fallbackValue = fallbackEval.result?.value || {};
      rawItems = Array.isArray(fallbackValue.items) ? fallbackValue.items : [];
      fallbackCount = rawItems.length;
      if (!lastTitle) lastTitle = String(fallbackValue.title || "");
      if (!lastUrl) lastUrl = String(fallbackValue.url || targetUrl);
    }
    const debugQuery = {
      query: searchQuery,
      query_index: Number(queryIndex || 0),
      target_id: String(researchTab.targetId || ""),
      filter_applied: Boolean(filterState?.filter_applied),
      progress: debugProgress,
      filterState,
      primaryCount: Array.isArray(value.items) ? value.items.length : 0,
      fallbackCount,
      candidateCap,
      timing: {
        started_at: timing.started_at,
        navigate_started_at: timing.navigate_started_at,
        first_scroll_started_at: timing.first_scroll_started_at,
        first_scroll_done_at: timing.first_scroll_done_at,
        settle_done_at: timing.settle_done_at,
        extraction_started_at: timing.extraction_started_at,
        extraction_done_at: timing.extraction_done_at,
        first_scroll_ms: timing.first_scroll_started_at && timing.first_scroll_done_at
          ? timing.first_scroll_done_at - timing.first_scroll_started_at
          : 0,
        settle_ms: timing.navigate_started_at && timing.settle_done_at
          ? timing.settle_done_at - timing.navigate_started_at
          : 0,
        extraction_ms: timing.extraction_started_at && timing.extraction_done_at
          ? timing.extraction_done_at - timing.extraction_started_at
          : 0,
        total_ms: timing.extraction_done_at
          ? timing.extraction_done_at - timing.started_at
          : Date.now() - timing.started_at,
      },
      sampleText: String((rawItems[0]?.text || rawItems[0]?.excerpt || "")).slice(0, 180),
      sampleAuthor: String(rawItems[0]?.author || ""),
    };
    rawItems.sort((left, right) => {
      const leftY = Number(left?.story_bounds?.y ?? left?.bounds?.y ?? 0);
      const rightY = Number(right?.story_bounds?.y ?? right?.bounds?.y ?? 0);
      if (leftY !== rightY) return leftY - rightY;
      const leftX = Number(left?.story_bounds?.x ?? left?.bounds?.x ?? 0);
      const rightX = Number(right?.story_bounds?.x ?? right?.bounds?.x ?? 0);
      return leftX - rightX;
    });
    for (const item of rawItems) {
      let finalItem = {
        ...item,
        text_source: "dom",
        ocr_fallback_used: false,
        ocr_confidence: "",
        ocr_reason: "",
      };
      if (!facebookDomLooksComplete(finalItem, plan.profile)) {
        const imageBounds = item && typeof item === "object" ? item.image_bounds : null;
        const storyBounds = item && typeof item === "object" ? item.story_bounds : null;
        const fallbackBounds = item && typeof item === "object" ? item.bounds : null;
        const captureCandidates = [
          { kind: "ocr_image", bounds: imageBounds },
          { kind: "ocr_story", bounds: storyBounds },
          { kind: "ocr_context", bounds: fallbackBounds },
        ];
        for (const candidate of captureCandidates) {
          const bounds = candidate.bounds;
          const clipWidth = Number(bounds?.width || 0);
          const clipHeight = Number(bounds?.height || 0);
          if (clipWidth < 260 || clipHeight < 140) continue;
          try {
            const screenshot = await page.call("Page.captureScreenshot", {
              format: "png",
              clip: {
                x: Number(bounds.x || 0),
                y: Number(bounds.y || 0),
                width: clipWidth,
                height: clipHeight,
                scale: 1,
              },
              captureBeyondViewport: true,
            });
            const ocr = await ocrExtractFacebookPost({
              imageBase64: String(screenshot.data || ""),
              query,
              url: String(value.url || targetUrl),
              title: String(value.title || ""),
              excerpt: String(item.excerpt || item.text || ""),
            });
            if (ocr.ok && ocr.text) {
              finalItem = {
                ...finalItem,
                text: ocr.text,
                excerpt: ocr.text.slice(0, 320),
                text_source: candidate.kind,
                ocr_fallback_used: true,
                ocr_confidence: ocr.confidence,
                ocr_reason: ocr.reason,
              };
              break;
            }
          } catch {
            // keep trying next capture region
          }
        }
      }
      const scored = scoreFacebookItem(finalItem, plan.profile);
      collected.push({
        ...finalItem,
        query_used: searchQuery,
        source_target_id: String(researchTab.targetId || ""),
        source_state: {
          url: String(sourceState.href || targetUrl),
          scrollY: Math.max(0, Number(sourceState.scrollY || 0) || 0),
        },
        score: scored.score,
        reasons: scored.reasons,
        relevant: scored.isRelevant,
        recency_hours: scored.recency_hours,
        time_parse_source: scored.time_parse_source || "",
      });
    }
    const gateProfile = plan?.profile || {};
    const inPlaceCandidates = collected
      .filter((item) => String(item.source_target_id || "") === String(researchTab.targetId || ""))
      .filter((item) => candidatePassesClickGate(item, gateProfile))
      .sort((left, right) => scoreFacebookResolverCandidate(right, searchQuery) - scoreFacebookResolverCandidate(left, searchQuery))
      .slice(0, Math.max(1, Number(process.env.FACEBOOK_RESOLVER_READS_PER_TAB || 2)));
    debugQuery.inplaceCandidateCount = inPlaceCandidates.length;
    for (const item of inPlaceCandidates) {
      try {
        const resolved = await resolveFacebookCandidateInPlace(page, debugPort, browser, item);
        if (resolved?.resolver_ok && String(resolved.body_text_clean || "").trim().length >= 80) {
          const rescored = scoreFacebookItem(resolved, gateProfile);
          resolvedInPlace.push({
            ...resolved,
            score: rescored.score,
            reasons: rescored.reasons,
            relevant: rescored.isRelevant,
            recency_hours: rescored.recency_hours,
            time_parse_source: rescored.time_parse_source || "",
          });
        }
      } catch {}
    }
    debugQuery.inplaceResolvedCount = resolvedInPlace.length;
    return {
      collected,
      resolvedInPlace,
      debugQuery,
      filterState,
      completedAt: Date.now(),
      queryIndex: Number(queryIndex || 0),
      targetId: String(researchTab.targetId || ""),
      sourceState: {
        url: String(sourceState.href || targetUrl),
        scrollY: Math.max(0, Number(sourceState.scrollY || 0) || 0),
      },
      lastTitle,
      lastUrl,
      needsLogin,
    };
  } finally {
    try {
      page.close();
    } catch {}
  }
}

async function extractFacebookExtensionStyleCandidates(page, searchQuery, candidateCap = 30) {
  const evalResult = await page.call("Runtime.evaluate", {
    expression: `
(() => {
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const normalizeLoose = (value) => normalize(value).toLowerCase();
  const makeKey = (...parts) => normalize(parts.filter(Boolean).join(' | ')).toLowerCase().slice(0, 400);
  const q = normalizeLoose(${JSON.stringify(searchQuery)});
  const roleTerms = /\\bdevops\\b|\\bsre\\b|cloud|platform|infra|sysadmin/.test(q)
    ? ['devops','sre','cloud','platform','infra','sysadmin','kubernetes','docker','terraform']
    : [];
  const seniorityTerms = /intern|thuc tap|thực tập|fresher|junior|entry level|khong can kinh nghiem|không cần kinh nghiệm/.test(q)
    ? ['intern','thực tập','thực tập sinh','fresher','junior','entry level','không cần kinh nghiệm']
    : [];
  const locationTerms = /hcm|tphcm|tp hcm|ho chi minh|hồ chí minh|sai gon|sài gòn|hcmc/.test(q)
    ? ['hcm','tphcm','tp hcm','ho chi minh','hồ chí minh','sai gon','sài gòn','hcmc']
    : [];
  const positive = [...new Set([...roleTerms, ...seniorityTerms, ...locationTerms, 'tuyển', 'tuyển dụng', 'hiring', 'apply', 'ứng tuyển', 'jd', 'job description'])];
  const negative = ['senior','lead','manager','director','looking for job','tìm việc'];
  const isVisible = (el) => {
    const rect = el?.getBoundingClientRect?.();
    if (!rect) return false;
    if (rect.width <= 0 || rect.height <= 0) return false;
    if (rect.bottom < 0 || rect.top > window.innerHeight) return false;
    return true;
  };
  const findSeeMoreButtons = (root) => [...root.querySelectorAll("div[role='button'], span[role='button'], a[role='link'], button")]
    .filter((el) => {
      if (!isVisible(el)) return false;
      const text = normalize(el.textContent || el.innerText || '');
      const aria = normalize(el.getAttribute('aria-label') || '');
      return /^(see more|xem thêm|xem them|more)$/i.test(text) || /^(see more|xem thêm|xem them)$/i.test(aria);
    });
  for (const node of [...document.querySelectorAll("[data-ad-rendering-role='story_message'], div[data-ad-preview='message'], div[data-ad-comet-preview='message']")]) {
    for (const button of findSeeMoreButtons(node).slice(0, 3)) {
      try { button.click(); } catch {}
    }
  }
  const storyNodes = [...document.querySelectorAll("[data-ad-rendering-role='story_message'], div[data-ad-preview='message'], div[data-ad-comet-preview='message']")];
  const seen = new Set();
  const items = [];
  const collectLargeImageBounds = (root) => {
    let best = null;
    let bestArea = 0;
    for (const node of [...root.querySelectorAll('img, [role="img"]')]) {
      const rect = node.getBoundingClientRect?.();
      if (!rect || rect.width < 120 || rect.height < 120) continue;
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
  };
  const findCommentButton = (root) => [...root.querySelectorAll("div[role='button'], span[role='button'], a[role='link'], [aria-label], button")]
    .find((el) => {
      const text = normalize(el.textContent || el.innerText || '');
      const aria = normalize(el.getAttribute('aria-label') || '');
      return /(^comment$|^bình luận$|^binh luan$|leave a comment|write a comment)/i.test(text)
        || /(^comment$|^bình luận$|^binh luan$|leave a comment|write a comment)/i.test(aria);
    }) || null;
  const scoreLink = (href, text) => {
    let n = 0;
    if (/\\/permalink\\//i.test(href) || /story_fbid=/i.test(href) || /\\/posts\\//i.test(href) || /permalink\\.php/i.test(href)) n += 20;
    else if (/\\/photo\\/\\?/i.test(href) || /[?&]fbid=/i.test(href)) n += 10;
    if (/facebook\\.com\\/groups\\//i.test(href)) n += 4;
    if (/comment|share|like|join/i.test(text)) n -= 5;
    return n;
  };
  const extractBestLink = (root) => {
    const ranked = [...root.querySelectorAll('a[href]')]
      .map((a) => {
        const href = String(a.href || '');
        const text = normalize(a.textContent || a.innerText || a.getAttribute('aria-label') || '');
        return { href, text, score: scoreLink(href, text) };
      })
      .filter((item) => /facebook\\.com/i.test(item.href))
      .sort((a, b) => b.score - a.score);
    return ranked[0]?.href || '';
  };
  for (const [index, story] of storyNodes.entries()) {
    let current = story;
    let best = story;
    let hops = 0;
    while (current && hops < 8) {
      const text = normalize(current.innerText || '');
      const links = current.querySelectorAll ? current.querySelectorAll('a[href]').length : 0;
      if (text.length >= 80 && text.length <= 7000) best = current;
      if (links >= 3) {
        best = current;
        break;
      }
      current = current.parentElement;
      hops += 1;
    }
    const text = normalize(story.innerText || best.innerText || '');
    const excerpt = text.slice(0, 320);
    const url = extractBestLink(best);
    const commentButton = findCommentButton(best);
    const imageBounds = collectLargeImageBounds(best);
    const key = makeKey(url, excerpt.slice(0, 180));
    if (!key || seen.has(key)) continue;
    seen.add(key);
    const hay = normalizeLoose(text);
    const roleHit = roleTerms.some((term) => hay.includes(normalizeLoose(term)));
    const seniorityHit = seniorityTerms.some((term) => hay.includes(normalizeLoose(term)));
    const locationHit = !locationTerms.length || locationTerms.some((term) => hay.includes(normalizeLoose(term)));
    const positiveHits = positive.filter((term) => hay.includes(normalizeLoose(term)));
    const negativeHits = negative.filter((term) => hay.includes(normalizeLoose(term)));
    let score = 0;
    if (roleHit) score += 6;
    if (seniorityHit) score += 4;
    if (locationHit) score += 2;
    score += Math.min(4, positiveHits.length);
    score -= Math.min(6, negativeHits.length * 3);
    if (imageBounds) score += 1.5;
    if (commentButton) score += 2;
    items.push({
      cache_key: key,
      dom_index: index,
      query_used: ${JSON.stringify(searchQuery)},
      url,
      text,
      excerpt,
      has_comment: Boolean(commentButton),
      has_large_image: Boolean(imageBounds),
      image_bounds: imageBounds,
      score,
      matches: {
        roleHit,
        seniorityHit,
        locationHit,
        positiveHits,
        negativeHits,
      },
    });
  }
  return {
    title: document.title,
    url: location.href,
    total_found: items.length,
    items: items.slice(0, ${Math.max(1, Math.min(Number(candidateCap || 30), 80))}),
  };
})()
    `,
    returnByValue: true,
    awaitPromise: true,
  });
  return evalResult.result?.value || { title: "", url: "", total_found: 0, items: [] };
}

async function requestInstalledFacebookExtension(page, request, timeoutMs = 45000) {
  await page.call("Runtime.enable");
  const nonce = `fb_ext_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
  const evalResult = await page.call("Runtime.evaluate", {
    expression: `
(async () => {
  const nonce = ${JSON.stringify(nonce)};
  const request = ${JSON.stringify(request || {})};
  return await new Promise((resolve) => {
    const timer = setTimeout(() => {
      window.removeEventListener("message", onMessage);
      resolve({ ok: false, error: "extension_request_timeout" });
    }, ${Math.max(1000, Math.min(Number(timeoutMs || 45000), 120000))});
    function onMessage(event) {
      if (event.source !== window) return;
      const data = event.data || {};
      if (data.__fbBridgeExtensionResponse === true && String(data.nonce || "") === nonce) {
        clearTimeout(timer);
        window.removeEventListener("message", onMessage);
        resolve(data.payload || { ok: false, error: "empty_extension_payload" });
      }
    }
    window.addEventListener("message", onMessage);
    window.postMessage({ __fbBridgeExtensionRequest: true, nonce, request }, "*");
  });
})()
    `,
    returnByValue: true,
    awaitPromise: true,
  });
  return evalResult.result?.value || { ok: false, error: "extension_request_failed" };
}

async function waitForInstalledFacebookExtensionReady(page, maxAttempts = 3) {
  for (let attempt = 0; attempt < Math.max(1, Number(maxAttempts || 5)); attempt += 1) {
    const response = await requestInstalledFacebookExtension(page, {
      type: "bridge-extract-visible",
      // Keep extension-side extraction broad; backend/AI will filter by the real query.
      query: "",
    }, 6000).catch(() => ({ ok: false, error: "extension_request_failed" }));
    if (response?.ok) {
      return response;
    }
    await waitForFacebookSearchSettled(page, {
      minWaitMs: 500,
      maxWaitMs: 2500,
      pollMs: 250,
      stableRounds: 1,
      scroll: false,
    });
  }
  return { ok: false, error: "extension_not_ready" };
}

async function processFacebookExtensionHarvestQuery({
  searchQuery,
  queryIndex = 0,
  debugPort,
  scrollRounds = 4,
  candidateCap = 30,
}) {
  const targetUrl = buildFacebookSearchUrl(searchQuery, null);
  const researchTab = await openFreshResearchTab(debugPort, targetUrl);
  const page = researchTab.page;
  try {
    await page.call("Page.enable");
    await page.call("Page.navigate", { url: targetUrl });
    await waitForFacebookSearchSettled(page, {
      minWaitMs: 1200,
      maxWaitMs: 8000,
      pollMs: 350,
      stableRounds: 2,
      scroll: false,
    });
    const extensionReady = await waitForInstalledFacebookExtensionReady(page, 6);
    if (!extensionReady?.ok) {
      return {
        query: searchQuery,
        query_index: Number(queryIndex || 0),
        target_id: String(researchTab.targetId || ""),
        title: "",
        url: targetUrl,
        total_found: 0,
        items: [],
        extractor_source: "extension",
        extension_error: String(extensionReady?.error || "extension_not_ready"),
      };
    }
    let extracted = await requestInstalledFacebookExtension(page, {
      type: "bridge-scroll-scan",
      // Keep extension-side extraction broad; backend/AI will filter by the real query.
      query: "",
      rounds: Math.max(1, Math.min(Number(scrollRounds || 4), 8)),
    }, 15000).catch(() => ({ ok: false, error: "extension_request_failed" }));
    if (!extracted?.ok) {
      return {
        query: searchQuery,
        query_index: Number(queryIndex || 0),
        target_id: String(researchTab.targetId || ""),
        title: "",
        url: targetUrl,
        total_found: 0,
        items: [],
        extractor_source: "extension",
        extension_error: String(extracted?.error || "extension_request_failed"),
      };
    }
    return {
      query: searchQuery,
      query_index: Number(queryIndex || 0),
      target_id: String(researchTab.targetId || ""),
      title: String(extracted.title || ""),
      url: String(extracted.url || targetUrl),
      total_found: Number(extracted.total_found || extracted.count || (Array.isArray(extracted.items) ? extracted.items.length : 0) || 0),
      items: Array.isArray(extracted.items) ? extracted.items : [],
      extractor_source: "extension",
      extension_error: "",
    };
  } finally {
    try {
      page.close();
    } catch {}
  }
}

async function facebookExtensionHarvest({
  query = "",
  browser = "brave",
  debugPort = 9222,
  profileDir = "",
  scrollRounds = 4,
}) {
  const maxQueries = Math.max(1, Math.min(Number(process.env.FACEBOOK_QUERY_MAX || 4), 8));
  const candidateCap = Math.max(15, Math.min(Number(process.env.FACEBOOK_EXTENSION_CANDIDATE_CAP || 30), 80));
  const ensurePromise = ensureDebugBrowser({
    browser,
    url: "https://www.facebook.com/",
    debugPort,
    profileDir,
  });
  const planPromise = fetchFacebookSearchPlan({ query, maxQueries });
  const [, plan] = await Promise.all([ensurePromise, planPromise]);
  await trimDebugPageTargetsButKeepOne(debugPort).catch(() => ({ keptTargetId: "" }));
  const queryConcurrency = Math.max(1, Math.min(Number(process.env.FACEBOOK_QUERY_TAB_CONCURRENCY || 4), Array.isArray(plan.queries) ? plan.queries.length : 1, 4));
  const perTabTimeoutMs = Math.max(15000, Math.min(Number(process.env.FACEBOOK_PER_TAB_TIMEOUT_MS || 30000), 120000));
  const queryResults = await mapWithConcurrency(plan.queries, queryConcurrency, async (searchQuery, queryIndex) => {
    const runHarvestAttempt = async () => withTimeout(processFacebookExtensionHarvestQuery({
      searchQuery,
      queryIndex,
      debugPort,
      scrollRounds,
      candidateCap,
    }), perTabTimeoutMs, `facebook-extension-harvest-timeout:${queryIndex}:${searchQuery}`);
    try {
      return await runHarvestAttempt();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const retryableTimeout = message.includes(`facebook-extension-harvest-timeout:${queryIndex}:`) || message.includes("extension_request_timeout");
      const retryableDebugRestart = message.includes("/json/version") || message.includes("Inspectable page not found");
      if (retryableTimeout || retryableDebugRestart) {
        try {
          if (retryableDebugRestart) {
            await ensureDebugBrowser({
              browser,
              url: "https://www.facebook.com/",
              debugPort,
              profileDir,
            });
          }
          const retried = await runHarvestAttempt();
          return {
            ...retried,
            retry_count: 1,
          };
        } catch (retryError) {
          const retryMessage = retryError instanceof Error ? retryError.message : String(retryError);
          return {
            query: searchQuery,
            query_index: Number(queryIndex || 0),
            target_id: "",
            title: "",
            url: "",
            total_found: 0,
            items: [],
            retry_count: 1,
            error: retryMessage,
          };
        }
      }
      return {
        query: searchQuery,
        query_index: Number(queryIndex || 0),
        target_id: "",
        title: "",
        url: "",
        total_found: 0,
        items: [],
        error: message,
      };
    }
  });
  const merged = [];
  const seen = new Set();
  for (const result of queryResults) {
    for (const item of result.items || []) {
      const key = String(item.cache_key || `${item.url || ""}::${normalizeText(item.excerpt || item.text || "").slice(0, 120)}`);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      merged.push({
        ...item,
        query_used: String(result.query || item.query_used || ""),
        target_id: String(result.target_id || ""),
      });
    }
  }
  merged.sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
  const artifact = {
    saved_at: new Date().toISOString(),
    source: "facebook_extension_style_harvest",
    query,
    queries_used: Array.isArray(plan.queries) ? plan.queries : [],
    parallel_tabs_used: queryConcurrency,
    total_found: merged.length,
    query_results: queryResults,
    items: merged,
  };
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const filename = `${stamp}_${toSafeFileStem(query || "facebook_harvest")}_extension_harvest.json`;
  const outputPath = path.join(artifactDir, filename);
  fs.writeFileSync(outputPath, JSON.stringify(artifact, null, 2), "utf8");
  return {
    ok: true,
    query,
    queries_used: artifact.queries_used,
    parallel_tabs_used: queryConcurrency,
    total_found: merged.length,
    path: outputPath,
    items: merged,
    top_items: merged.slice(0, 20),
    query_results: queryResults.map((result) => ({
      query: result.query,
      total_found: result.total_found,
      retry_count: Number(result.retry_count || 0),
      extractor_source: result.extractor_source || "",
      extension_error: result.extension_error || "",
      error: result.error || "",
    })),
  };
}

async function facebookResearch({
  query = "",
  browser = "brave",
  debugPort = 9222,
  limit = 8,
  scrollRounds = 4,
  profileDir = "",
}) {
  const demoMode = demoModeEnabled();
  const maxSearchQueries = Math.max(1, Math.min(Number(process.env.FACEBOOK_QUERY_MAX || (demoMode ? 4 : 4)), 6));
  const scanCandidateQuota = Math.max(10, Math.min(Number(process.env.FACEBOOK_SCAN_CANDIDATE_QUOTA || 14), 20));
  const resolverReadPerTab = Math.max(2, Math.min(Number(process.env.FACEBOOK_RESOLVER_READS_PER_TAB || 5), 7));
  const ensurePromise = ensureDebugBrowser({
    browser,
    url: "https://www.facebook.com/",
    debugPort,
    profileDir,
  });
  const planPromise = fetchFacebookSearchPlan({ query, maxQueries: maxSearchQueries });
  const [ensured, plan] = await Promise.all([ensurePromise, planPromise]);
  if (demoMode) {
    const demoQueries = buildFacebookDemoQueries(query);
    if (demoQueries.length) {
      plan.queries = demoQueries;
    }
  }
  const timeWindow = parseQueryTimeWindow(query);
  if (timeWindow) {
    plan.profile = { ...(plan.profile || {}), timeWindowHours: timeWindow.maxHours };
  }
  await trimDebugPageTargetsButKeepOne(debugPort).catch(() => ({ keptTargetId: "" }));
  try {
    const collected = [];
    const resolvedInline = [];
    const debugQueries = [];
    const debugFilterStates = [];
    let lastUrl = "";
    let lastTitle = "";
    let needsLogin = false;
    const queryConcurrency = Math.max(
      1,
      Math.min(
        Number(process.env.FACEBOOK_QUERY_TAB_CONCURRENCY || (demoMode ? 3 : 4)),
        Array.isArray(plan.queries) ? plan.queries.length : 1,
        demoMode ? 3 : 4,
      ),
    );
    const queryResults = await mapWithConcurrency(plan.queries, queryConcurrency, async (searchQuery, queryIndex) =>
      {
        try {
          return await withTimeout(processFacebookSearchQuery({
            searchQuery,
            queryIndex,
            browser,
            debugPort,
            limit,
            scanCandidateQuota,
            scrollRounds,
            plan,
            timeWindow,
          }), Number(process.env.FACEBOOK_PER_TAB_TIMEOUT_MS || 30000), `facebook-query-timeout:${queryIndex}:${searchQuery}`);
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          return {
            collected: [],
            debugQuery: {
              query: searchQuery,
              query_index: Number(queryIndex || 0),
              target_id: "",
              filter_applied: false,
              status: "error",
              error: message,
            },
            filterState: {
              ok: false,
              targetYears: timeWindow ? deriveFacebookFilterYears(timeWindow) : [],
              years: [],
              yearsVisible: [],
              filter_retry_count: 0,
              filter_applied: false,
              error: message,
            },
            completedAt: Date.now(),
            queryIndex: Number(queryIndex || 0),
            targetId: "",
            lastTitle: "",
            lastUrl: "",
            needsLogin: false,
            error: message,
          };
        }
      },
    );
    const tabDebugSummary = [];
    for (const result of queryResults) {
      if (!result) continue;
      if (Array.isArray(result.collected)) collected.push(...result.collected);
      if (Array.isArray(result.resolvedInPlace)) resolvedInline.push(...result.resolvedInPlace);
      if (result.debugQuery) debugQueries.push(result.debugQuery);
      if (result.filterState) {
        debugFilterStates.push({
          query: result.debugQuery?.query || "",
          query_index: Number(result.queryIndex || 0),
          target_id: String(result.targetId || ""),
          filter_applied: Boolean(result.filterState?.filter_applied),
          ...result.filterState,
        });
      }
      tabDebugSummary.push({
        query: result.debugQuery?.query || "",
        query_index: Number(result.queryIndex || 0),
        target_id: String(result.targetId || ""),
        status: result.error ? "error" : "ok",
        filter_applied: Boolean(result.filterState?.filter_applied),
        ui_filter_applied: Boolean(result.filterState?.ui_filter_applied),
        filter_source: String(result.filterState?.filter_source || ""),
        openedDatePosted: Boolean(result.filterState?.openedDatePosted),
        years: Array.isArray(result.filterState?.years) ? result.filterState.years : [],
        targetYears: Array.isArray(result.filterState?.targetYears) ? result.filterState.targetYears : [],
        filter_retry_count: Number(result.filterState?.filter_retry_count || 0),
        error: result.error || "",
      });
      if (result.lastTitle) lastTitle = result.lastTitle;
      if (result.lastUrl) lastUrl = result.lastUrl;
      needsLogin = needsLogin || Boolean(result.needsLogin);
    }

    const resolverQueueDebug = [];
    const deduped = [];
    const seen = new Set();
    for (const item of collected.sort((a, b) => Number(b.score || 0) - Number(a.score || 0))) {
      const key = `${item.url || ""}::${normalizeText(item.excerpt || item.text || "").slice(0, 120)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      deduped.push(item);
    }
    const queueCandidates = deduped
      .filter((item) => String(item.source_target_id || ""))
      .filter((item) => {
        const text = buildFacebookModelText(item, 700);
        return Boolean(item.raw_url || item.url) || text.length >= 40;
      })
      .slice(0, scanCandidateQuota * Math.max(1, plan.queries.length));
    const queueByTab = new Map();
    const queueByTabOriginalCounts = new Map();
    for (const item of queueCandidates) {
      const key = String(item.source_target_id || "");
      if (!queueByTab.has(key)) {
        queueByTab.set(key, []);
      }
      const bucket = queueByTab.get(key);
      if (bucket.length >= scanCandidateQuota) continue;
      bucket.push(item);
    }
    for (const [tabKey, bucket] of queueByTab.entries()) {
      queueByTabOriginalCounts.set(tabKey, bucket.length);
      const gatedBucket = bucket.filter((item) => candidatePassesClickGate(item, plan.profile || {}));
      const candidateSource = gatedBucket.length ? gatedBucket : bucket;
      const sortedBucket = [...candidateSource]
        .sort((left, right) => scoreFacebookResolverCandidate(right, query) - scoreFacebookResolverCandidate(left, query));
      queueByTab.set(tabKey, sortedBucket.slice(0, resolverReadPerTab));
    }
    const resolvedQueueItems = [...resolvedInline];
    const resolverOrderedResults = [...queryResults]
      .filter(Boolean)
      .sort((left, right) => Number(left?.completedAt || 0) - Number(right?.completedAt || 0));
    for (const result of resolverOrderedResults) {
      const tabKey = String(result?.targetId || "");
      const tabItems = queueByTab.get(tabKey) || [];
      resolverQueueDebug.push({
        query: String(result?.debugQuery?.query || ""),
        target_id: tabKey,
        candidate_count: Number(queueByTabOriginalCounts.get(tabKey) || tabItems.length),
        selected_count: tabItems.length,
        resolver_read_per_tab: resolverReadPerTab,
        completed_at: Number(result?.completedAt || 0),
        inline_resolved_count: Array.isArray(result?.resolvedInPlace) ? result.resolvedInPlace.length : 0,
      });
      try {
        await closeDebugTarget(debugPort, tabKey);
      } catch {}
    }
    const dedupedResolved = [];
    const resolvedSeen = new Set();
    for (const item of [...resolvedQueueItems, ...deduped].sort((a, b) => Number(b.score || 0) - Number(a.score || 0))) {
      const key = `${item.url || item.raw_url || ""}::${normalizeText(item.excerpt || item.text || "").slice(0, 120)}`;
      if (resolvedSeen.has(key)) continue;
      resolvedSeen.add(key);
      dedupedResolved.push(item);
    }
    const cappedLimit = Math.max(1, Math.min(20, Number(limit || 8)));
    const itemHasKnownRecency = (item) => !(item.recency_hours === null || item.recency_hours === undefined || item.recency_hours === "");
    const withinWindow = (item) => {
      if (!timeWindow) return true;
      if (!itemHasKnownRecency(item)) return false;
      const recency = Number(item.recency_hours);
      return Number.isFinite(recency) && recency >= 0 && recency <= timeWindow.maxHours;
    };
    const markTimeBucket = (item) => {
      if (!timeWindow) return { ...item, time_bucket: "none" };
      if (withinWindow(item)) return { ...item, time_bucket: "recent_confirmed" };
      if (itemHasKnownRecency(item)) return { ...item, time_bucket: "stale_confirmed" };
      return { ...item, time_bucket: "time_unknown" };
    };
    const bucketed = dedupedResolved.map(markTimeBucket);
    const confirmedRecent = bucketed.filter((item) => item.time_bucket === "recent_confirmed");
    const unknownStrong = bucketed.filter((item) => item.time_bucket === "time_unknown");
    const staleConfirmed = bucketed.filter((item) => item.time_bucket === "stale_confirmed");
    const filteredRaw = timeWindow ? [...confirmedRecent, ...unknownStrong] : bucketed;
    const candidateUniverse = filteredRaw.length ? filteredRaw : bucketed;
    const rankedUniverse = candidateUniverse
      .filter((item) => isUsableFacebookResultUrl(String(item.url || "")) || isUsableFacebookResultUrl(String(item.raw_url || "")))
      .map((item) => ({
        ...item,
        display_score: scoreFacebookDisplayCandidate(item, plan.profile || {}, query),
      }))
      .filter((item) => item.display_score >= 5 || isFacebookPhotoUrl(String(item.url || item.raw_url || "")))
      .sort((left, right) => {
        const scoreDiff = Number(right.display_score || 0) - Number(left.display_score || 0);
        if (scoreDiff) return scoreDiff;
        const rightPermalink = isFacebookPermalinkLike(String(right.url || right.raw_url || ""));
        const leftPermalink = isFacebookPermalinkLike(String(left.url || left.raw_url || ""));
        if (rightPermalink !== leftPermalink) return rightPermalink ? 1 : -1;
        const rightPhoto = isFacebookPhotoUrl(String(right.url || right.raw_url || ""));
        const leftPhoto = isFacebookPhotoUrl(String(left.url || left.raw_url || ""));
        if (rightPhoto !== leftPhoto) return rightPhoto ? -1 : 1;
        return 0;
      });
    const orderedItems = dedupeOrderedFacebookItems(selectSoftTargetItems(rankedUniverse, cappedLimit));
    const topItems = orderedItems.map((item, index) => ({
      url: isUsableFacebookResultUrl(String(item.url || "")) ? String(item.url || "") : "",
      raw_url: isUsableFacebookResultUrl(String(item.raw_url || "")) ? String(item.raw_url || "") : "",
      author: cleanFacebookDisplayText(buildFacebookDisplayTitle(item, index), 120),
      excerpt: cleanFacebookDisplayText(item.excerpt || item.text || "", 220),
      text: cleanFacebookDisplayText(item.text || item.excerpt || "", 420),
      time_hint: item.time_hint,
      query_used: item.query_used,
      score: item.score,
      reasons: item.reasons,
      recency_hours: item.recency_hours,
      time_parse_source: item.time_parse_source || "",
      time_bucket: item.time_bucket || "",
      text_source: item.text_source || "dom",
      ocr_fallback_used: Boolean(item.ocr_fallback_used),
      ocr_confidence: item.ocr_confidence || "",
      link_source: item.link_source || "",
      display_score: Number(item.display_score || item.score || 0),
      llm_score: 0,
      llm_verdict: "",
      llm_reason: "",
      llm_display_title: "",
      llm_display_summary: "",
    }));
    const displayResults = topItems.slice(0, 10).map((item, index) => ({
      rank: index + 1,
      author: item.author || `Bài ${index + 1}`,
      summary: cleanFacebookDisplayText(item.excerpt || item.text || "", 160),
      keep_reason: describeLocalKeepReason(item, plan.profile || {}),
      time_status: item.time_bucket || "",
      url: item.url || item.raw_url || "",
      raw_url: item.raw_url || "",
    }));

    return {
      browser,
      debug_port: debugPort,
      profile_dir: ensured.profile_dir,
      launched: ensured.launched,
      reused_research_tab: false,
      parallel_tabs_used: queryConcurrency,
      title: lastTitle,
      url: lastUrl,
      needs_login: needsLogin,
      total_found: collected.length,
      ocr_used_count: collected.filter((item) => item.ocr_fallback_used).length,
      queries_used: plan.queries,
      filtered_count: rankedUniverse.length,
      summary: {
        ...summarizeFacebookItems(topItems, plan.profile),
        time_window_hours: timeWindow ? timeWindow.maxHours : 0,
        time_window_label: timeWindow ? timeWindow.label : "",
        time_filtered_count: timeWindow ? confirmedRecent.length : 0,
        recent_confirmed_count: timeWindow ? confirmedRecent.length : 0,
        time_unknown_count: timeWindow ? unknownStrong.length : 0,
        stale_confirmed_count: timeWindow ? staleConfirmed.length : 0,
        planner_intent: plan.planner_intent || "",
        planner_criteria: plan.planner_criteria || [],
        planner_summary: plan.planner_summary || "",
        rerank_intent: "",
        rerank_criteria: [],
        rerank_summary: "",
        rerank_applied: false,
      },
      debug_queries: debugQueries,
      debug_filter_states: debugFilterStates,
      tab_debug_summary: tabDebugSummary,
      resolver_queue_debug: resolverQueueDebug,
      results: displayResults,
      items: topItems,
    };
  } finally {
    await closeAllDebugPageTargets(debugPort).catch(() => {});
  }
}

async function captureDebugBrowserScreenshot({ debugPort = 9222, targetDir = "", name = "" }) {
  const actualDir = targetDir || artifactDir;
  fs.mkdirSync(actualDir, { recursive: true });
  const safeName = (name || `debug_browser_${new Date().toISOString().replace(/[:.]/g, "-")}.png`).replace(/[^a-zA-Z0-9_.-]+/g, "_");
  const finalName = safeName.toLowerCase().endsWith(".png") ? safeName : `${safeName}.png`;
  const outputPath = path.join(actualDir, finalName);
  const targets = await getDebugTargets(debugPort);
  const pageTarget = (Array.isArray(targets) ? targets : []).find((item) => String(item.type || "") === "page" && item.webSocketDebuggerUrl);
  if (!pageTarget?.webSocketDebuggerUrl) {
    throw new Error(`No debug page target available on port ${debugPort}`);
  }
  const page = await connectPageCdp(String(pageTarget.webSocketDebuggerUrl));
  try {
    try {
      await page.call("Page.enable");
    } catch {}
    const screenshot = await page.call("Page.captureScreenshot", {
      format: "png",
      captureBeyondViewport: true,
      fromSurface: true,
    });
    fs.writeFileSync(outputPath, Buffer.from(String(screenshot.data || ""), "base64"));
    const meta = await page.call("Runtime.evaluate", {
      expression: "({ title: document.title || '', href: location.href || '' })",
      returnByValue: true,
      awaitPromise: true,
    }).catch(() => ({ result: { value: {} } }));
    const value = meta?.result?.value || {};
    return {
      path: outputPath,
      title: String(value.title || ""),
      url: String(value.href || ""),
      full_screen: false,
      rect: { X: 0, Y: 0, Width: 0, Height: 0 },
      backend: "debug-cdp",
      target_id: String(pageTarget.id || ""),
      debug_port: Number(debugPort || 9222),
    };
  } finally {
    try {
      page.close();
    } catch {}
  }
}

async function captureScreenshot({ pid = 0, fullScreen = false, name = "", targetDir = "", debugPort = 9222 }) {
  const actualDir = targetDir || artifactDir;
  fs.mkdirSync(actualDir, { recursive: true });
  const safeName = (name || `host_screenshot_${new Date().toISOString().replace(/[:.]/g, "-")}.png`).replace(/[^a-zA-Z0-9_.-]+/g, "_");
  const finalName = safeName.toLowerCase().endsWith(".png") ? safeName : `${safeName}.png`;
  const outputPath = path.join(actualDir, finalName);
  if (isLinux) {
    try {
      const tempXwd = path.join(actualDir, `${path.basename(finalName, ".png")}.xwd`);
      await runCommand("xwd", ["-root", "-silent", "-display", getLinuxDisplayEnv().DISPLAY || ":0", "-out", tempXwd], {
        env: getLinuxDisplayEnv(),
      });
      await runCommand("convert", [tempXwd, outputPath], {
        env: getLinuxDisplayEnv(),
      });
      try {
        fs.unlinkSync(tempXwd);
      } catch {}
      return {
        path: outputPath,
        title: "",
        pid: Number(pid || 0),
        full_screen: true,
        rect: { X: 0, Y: 0, Width: 0, Height: 0 },
        backend: "xwd-convert",
      };
    } catch (error) {
      const fallback = await captureDebugBrowserScreenshot({
        debugPort,
        targetDir: actualDir,
        name: finalName,
      });
      return {
        ...fallback,
        fallback_reason: error instanceof Error ? error.message : String(error),
      };
    }
  }
  const psOutput = outputPath.replace(/'/g, "''");
  const psPid = Number(pid || 0);
  const full = fullScreen ? "$true" : "$false";
  const script = `
$ErrorActionPreference='Stop'
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32Window {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
"@
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
try { [Win32Window]::SetProcessDPIAware() | Out-Null } catch {}
$fullScreen = ${full}
$targetPid = ${psPid}
$output = '${psOutput}'
$hwnd = [IntPtr]::Zero
$title = ''
if (-not $fullScreen -and $targetPid -gt 0) {
  $proc = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
  if ($proc -and $proc.MainWindowHandle -and $proc.MainWindowHandle -ne 0) {
    $hwnd = $proc.MainWindowHandle
    $title = $proc.MainWindowTitle
  }
}
if (-not $fullScreen -and $hwnd -eq [IntPtr]::Zero) { $hwnd = [Win32Window]::GetForegroundWindow() }
if ($fullScreen) {
  $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
  $x=$bounds.X; $y=$bounds.Y; $w=$bounds.Width; $h=$bounds.Height
} else {
  $rect = New-Object RECT
  if ($hwnd -ne [IntPtr]::Zero -and [Win32Window]::GetWindowRect($hwnd, [ref]$rect)) {
    $x=$rect.Left; $y=$rect.Top; $w=[Math]::Max(1, $rect.Right-$rect.Left); $h=[Math]::Max(1, $rect.Bottom-$rect.Top)
  } else {
    $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $x=$bounds.X; $y=$bounds.Y; $w=$bounds.Width; $h=$bounds.Height
  }
}
$bitmap = New-Object System.Drawing.Bitmap $w, $h
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
try {
  $graphics.CopyFromScreen($x, $y, 0, 0, $bitmap.Size)
  $bitmap.Save($output, [System.Drawing.Imaging.ImageFormat]::Png)
} finally {
  $graphics.Dispose()
  $bitmap.Dispose()
}
[pscustomobject]@{
  path=$output
  title=$title
  pid=$targetPid
  full_screen=$fullScreen
  rect=@{X=$x;Y=$y;Width=$w;Height=$h}
} | ConvertTo-Json -Depth 6 -Compress
`;
  const raw = await runPowerShell(script);
  return JSON.parse(raw);
}

async function setClipboardText(value = "") {
  if (isLinux) {
    const payload = JSON.stringify(String(value || ""));
    const code = `
import json, tkinter as tk
root = tk.Tk()
root.withdraw()
root.clipboard_clear()
root.clipboard_append(json.loads(${JSON.stringify(payload)}))
root.update()
root.destroy()
print('{"ok": true}')
`;
    const raw = await pythonCodeEval(code);
    return JSON.parse(raw || "{}");
  }
  const escaped = String(value || "").replace(/'/g, "''");
  const script = `
$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationCore
[System.Windows.Clipboard]::SetText('${escaped}')
[pscustomobject]@{ ok = $true } | ConvertTo-Json -Compress
`;
  const raw = await runPowerShell(script);
  return JSON.parse(raw || "{}");
}

async function getClipboardText() {
  if (isLinux) {
    const code = `
import json, tkinter as tk
root = tk.Tk()
root.withdraw()
text = ""
try:
    text = root.clipboard_get()
except Exception:
    text = ""
root.destroy()
print(json.dumps({"text": text}, ensure_ascii=False))
`;
    const raw = await pythonCodeEval(code);
    const parsed = JSON.parse(raw || "{}");
    return String(parsed.text || "");
  }
  const script = `
$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationCore
$text = ''
try { $text = [System.Windows.Clipboard]::GetText() } catch { $text = '' }
[pscustomobject]@{ text = $text } | ConvertTo-Json -Compress
`;
  const raw = await runPowerShell(script);
  const parsed = JSON.parse(raw || "{}");
  return String(parsed.text || "");
}

async function nativeClickAt(x = 0, y = 0) {
  const px = Number(x || 0);
  const py = Number(y || 0);
  if (isLinux) {
    const code = `
import ctypes, ctypes.util, json, time
x = int(round(${JSON.stringify(px)}))
y = int(round(${JSON.stringify(py)}))
libX11 = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11"))
libXtst = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Xtst"))
libX11.XOpenDisplay.restype = ctypes.c_void_p
dpy = libX11.XOpenDisplay(None)
if not dpy:
    raise SystemExit("Cannot open X display")
root = libX11.XDefaultRootWindow(ctypes.c_void_p(dpy))
libXtst.XTestFakeMotionEvent(ctypes.c_void_p(dpy), -1, x, y, 0)
libXtst.XTestFakeButtonEvent(ctypes.c_void_p(dpy), 1, True, 0)
libXtst.XTestFakeButtonEvent(ctypes.c_void_p(dpy), 1, False, 0)
libX11.XFlush(ctypes.c_void_p(dpy))
libX11.XCloseDisplay(ctypes.c_void_p(dpy))
print(json.dumps({"ok": True, "x": x, "y": y}))
`;
    const raw = await pythonCodeEval(code);
    return JSON.parse(raw || "{}");
  }
  const script = `
$ErrorActionPreference='Stop'
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class NativeMouse {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
}
"@
[NativeMouse]::SetCursorPos(${Math.round(px)}, ${Math.round(py)}) | Out-Null
Start-Sleep -Milliseconds 120
[NativeMouse]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 60
[NativeMouse]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
[pscustomobject]@{ ok = $true; x = ${Math.round(px)}; y = ${Math.round(py)} } | ConvertTo-Json -Compress
`;
  const raw = await runPowerShell(script);
  return JSON.parse(raw || "{}");
}

async function nativeMoveMouse(x = 0, y = 0) {
  const px = Number(x || 0);
  const py = Number(y || 0);
  if (isLinux) {
    const code = `
import ctypes, ctypes.util, json
x = int(round(${JSON.stringify(px)}))
y = int(round(${JSON.stringify(py)}))
libX11 = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11"))
libXtst = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Xtst"))
libX11.XOpenDisplay.restype = ctypes.c_void_p
dpy = libX11.XOpenDisplay(None)
if not dpy:
    raise SystemExit("Cannot open X display")
libXtst.XTestFakeMotionEvent(ctypes.c_void_p(dpy), -1, x, y, 0)
libX11.XFlush(ctypes.c_void_p(dpy))
libX11.XCloseDisplay(ctypes.c_void_p(dpy))
print(json.dumps({"ok": True, "x": x, "y": y}))
`;
    const raw = await pythonCodeEval(code);
    return JSON.parse(raw || "{}");
  }
  return { ok: false, reason: "move_mouse_not_implemented_for_platform", x: px, y: py };
}

async function cdpClickAt(page, x = 0, y = 0) {
  const px = Number(x || 0);
  const py = Number(y || 0);
  await page.call("Input.dispatchMouseEvent", {
    type: "mouseMoved",
    x: px,
    y: py,
    button: "none",
  });
  await page.call("Input.dispatchMouseEvent", {
    type: "mousePressed",
    x: px,
    y: py,
    button: "left",
    clickCount: 1,
  });
  await page.call("Input.dispatchMouseEvent", {
    type: "mouseReleased",
    x: px,
    y: py,
    button: "left",
    clickCount: 1,
  });
  return { ok: true, x: px, y: py };
}

function sameFacebookSearchState(currentHref = "", targetState = null) {
  try {
    const targetUrl = typeof targetState === "string" ? targetState : String(targetState?.url || "");
    if (!currentHref || !targetUrl) return false;
    const current = new URL(String(currentHref));
    const target = new URL(String(targetUrl));
    if (current.pathname !== target.pathname) return false;
    const currentQ = String(current.searchParams.get("q") || "").trim();
    const targetQ = String(target.searchParams.get("q") || "").trim();
    const currentFilters = String(current.searchParams.get("filters") || "").trim();
    const targetFilters = String(target.searchParams.get("filters") || "").trim();
    return currentQ === targetQ && currentFilters === targetFilters;
  } catch {
    return false;
  }
}

async function returnFromFacebookDetail(page, targetState = "") {
  const targetUrl = typeof targetState === "string" ? targetState : String(targetState?.url || "");
  const targetScrollY = Math.max(0, Number(typeof targetState === "object" ? targetState?.scrollY : 0) || 0);
  const before = await page.call("Runtime.evaluate", {
    expression: `
(() => ({
  href: location.href,
  hasDialog: !!document.querySelector('div[role="dialog"]')
}))()
    `,
    returnByValue: true,
    awaitPromise: true,
  });
  const beforeValue = before.result?.value || {};
  const currentHref = String(beforeValue.href || "");
  const hasDialog = Boolean(beforeValue.hasDialog);
  if (hasDialog) {
    await page.call("Runtime.evaluate", {
      expression: `
(() => {
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const dialog = document.querySelector('div[role="dialog"]');
  if (!dialog) return { ok:false, reason:'no-dialog' };
  const buttons = [...dialog.querySelectorAll('div[role="button"], span[role="button"], button, [aria-label], svg')];
  const header = dialog.querySelector('[role="banner"], [data-pagelet], div') || dialog;
  const dialogRect = dialog.getBoundingClientRect();
  const closeHost = buttons.find((el) => {
    const label = normalize(el.getAttribute?.('aria-label') || el.textContent || el.innerText || '');
    return /^(close|đóng|dong|cancel|dismiss|x)$/.test(label);
  });
  const topRightHost = buttons
    .map((el) => {
      const host = el.closest?.('div[role="button"], span[role="button"], button, [aria-label]') || el;
      const rect = host.getBoundingClientRect?.();
      if (!rect || rect.width < 16 || rect.height < 16) return null;
      const distanceRight = Math.abs(rect.right - dialogRect.right);
      const distanceTop = Math.abs(rect.top - dialogRect.top);
      return { host, rect, distanceRight, distanceTop };
    })
    .filter(Boolean)
    .filter((item) => item.distanceRight <= 80 && item.distanceTop <= 80)
    .sort((left, right) => (left.distanceRight + left.distanceTop) - (right.distanceRight + right.distanceTop))[0]?.host || null;
  const clickable =
    closeHost?.closest?.('div[role="button"], span[role="button"], button, [aria-label]') ||
    topRightHost ||
    closeHost ||
    null;
  if (!clickable || !clickable.click) return { ok:false, reason:'no-close-button' };
  clickable.click();
  return {
    ok:true,
    closeText: normalize(clickable.getAttribute?.('aria-label') || clickable.textContent || clickable.innerText || ''),
  };
})()
      `,
      returnByValue: true,
      awaitPromise: true,
    });
    await sleep(900);
  }
  const afterClose = await page.call("Runtime.evaluate", {
    expression: "location.href",
    returnByValue: true,
    awaitPromise: true,
  });
  const afterHref = String(afterClose.result?.value || "");
  if (targetUrl && afterHref && !sameFacebookSearchState(afterHref, targetState) && !afterHref.includes("/search/posts/")) {
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        await page.call("Runtime.evaluate", {
          expression: "history.back(); 'ok';",
          returnByValue: true,
          awaitPromise: true,
        });
      } catch {}
      await waitForFacebookSearchSettled(page, {
        minWaitMs: 1200,
        maxWaitMs: 10000,
        pollMs: 900,
        stableRounds: 1,
        scroll: false,
      });
      const backState = await page.call("Runtime.evaluate", {
        expression: "location.href",
        returnByValue: true,
        awaitPromise: true,
      });
      const backHref = String(backState.result?.value || "");
      if (sameFacebookSearchState(backHref, targetState)) break;
    }
  }
  const finalState = await page.call("Runtime.evaluate", {
    expression: "({ href: location.href, scrollY: window.scrollY || window.pageYOffset || 0 })",
    returnByValue: true,
    awaitPromise: true,
  });
  const finalValue = finalState.result?.value || {};
  const finalHref = String(finalValue.href || "");
  if (sameFacebookSearchState(finalHref, targetState)) {
    if (targetScrollY > 0) {
      try {
        await page.call("Runtime.evaluate", {
          expression: `window.scrollTo(0, ${Math.max(0, Math.round(targetScrollY))}); 'ok';`,
          returnByValue: true,
          awaitPromise: true,
        });
      } catch {}
      await sleep(300);
    }
    return { ok: true, restored: "history" };
  }
  if (targetUrl && finalHref && !sameFacebookSearchState(finalHref, targetState) && !finalHref.includes("/search/posts/")) {
    await page.call("Page.navigate", { url: targetUrl });
    await waitForFacebookSearchSettled(page, {
      minWaitMs: 1500,
      maxWaitMs: 12000,
      pollMs: 900,
      stableRounds: 1,
      scroll: false,
    });
    if (targetScrollY > 0) {
      try {
        await page.call("Runtime.evaluate", {
          expression: `window.scrollTo(0, ${Math.max(0, Math.round(targetScrollY))}); 'ok';`,
          returnByValue: true,
          awaitPromise: true,
        });
      } catch {}
      await sleep(300);
    }
    return { ok: true, restored: "navigate" };
  }
  return { ok: true, restored: "close" };
}

async function restoreFacebookSearchTabState(page, targetState = {}, candidate = {}) {
  const targetUrl = String(targetState?.url || "");
  if (!targetUrl) return { ok: false, reason: "missing-target-url" };
  await withTimeout(page.call("Page.navigate", { url: targetUrl }), 8000, "restore-search-navigate");
  await withTimeout(waitForFacebookSearchSettled(page, {
    minWaitMs: 1400,
    maxWaitMs: 12000,
    pollMs: 900,
    stableRounds: 1,
    scroll: false,
  }), 15000, "restore-search-settle");
  const scrollToY = Math.max(
    0,
    Number(candidate?.story_bounds?.y || candidate?.bounds?.y || targetState?.scrollY || 0) - 240,
  );
  try {
    await withTimeout(page.call("Runtime.evaluate", {
      expression: `window.scrollTo(0, ${Math.max(0, Math.round(scrollToY))}); 'ok';`,
      returnByValue: true,
      awaitPromise: true,
    }), 5000, "restore-search-scroll");
    await sleep(400);
  } catch {}
  return { ok: true, url: targetUrl, scrollY: scrollToY };
}

async function resolveFacebookCandidateInPlace(pageRef, debugPort, browser, candidate = {}) {
  const sourceState = candidate.source_state && typeof candidate.source_state === "object"
    ? candidate.source_state
    : { url: "", scrollY: 0 };
  if (sourceState.url) {
    const currentState = await withTimeout(pageRef.call("Runtime.evaluate", {
      expression: "({ href: location.href, scrollY: window.scrollY || window.pageYOffset || 0 })",
      returnByValue: true,
      awaitPromise: true,
    }), 8000, `inplace-read-current-state-timeout:${candidate.dom_index || 0}`);
    const currentValue = currentState.result?.value || {};
    if (!sameFacebookSearchState(String(currentValue.href || ""), sourceState)) {
      await withTimeout(pageRef.call("Page.navigate", { url: String(sourceState.url || "") }), 8000, `inplace-read-navigate-timeout:${candidate.dom_index || 0}`);
      await withTimeout(waitForFacebookSearchSettled(pageRef, {
        minWaitMs: 800,
        maxWaitMs: 7000,
        pollMs: 600,
        stableRounds: 0,
        scroll: false,
      }), 10000, `inplace-read-settle-timeout:${candidate.dom_index || 0}`);
    }
    const scrollToY = Math.max(
      0,
      Number(candidate?.story_bounds?.y || candidate?.bounds?.y || sourceState.scrollY || 0) - 240,
    );
    try {
      await withTimeout(pageRef.call("Runtime.evaluate", {
        expression: `window.scrollTo(0, ${Math.max(0, Math.round(scrollToY))}); 'ok';`,
        returnByValue: true,
        awaitPromise: true,
      }), 5000, `inplace-read-scroll-timeout:${candidate.dom_index || 0}`);
      await sleep(250);
    } catch {}
  }
  let resolvedDetail = await withTimeout(resolveFacebookCommentPermalink(pageRef, {
    domIndex: Number(candidate.dom_index || 0),
    browser,
    expectedText: String(candidate.excerpt || candidate.text || ""),
  }), 12000, `inplace-comment-resolve-timeout:${candidate.dom_index || 0}`);
  if ((!resolvedDetail.ok || !resolvedDetail.url) && candidate.raw_url) {
    resolvedDetail = await withTimeout(resolveFacebookPrimaryPermalink(pageRef, {
      domIndex: Number(candidate.dom_index || 0),
      expectedText: String(candidate.excerpt || candidate.text || ""),
    }), 10000, `inplace-primary-link-timeout:${candidate.dom_index || 0}`);
  }
  let resolvedText = "";
  let usedPhotoOcr = false;
  let photoOcrConfidence = "";
  const expectedNeedle = String(candidate.excerpt || candidate.text || "");
  if (resolvedDetail.ok && resolvedDetail.url) {
    resolvedText = [
      cleanFacebookDetailBodyText(String(resolvedDetail.body_text || "")),
      ...(Array.isArray(resolvedDetail.body_candidates) ? resolvedDetail.body_candidates.map((value) => cleanFacebookDetailBodyText(String(value || ""))) : []),
      cleanFacebookDetailBodyText(String(resolvedDetail.dialog_text || "")),
    ].find((value) => String(value || "").trim().length >= 80) || "";
    if ((!resolvedText || resolvedText.length < 80) && isFacebookPhotoUrl(String(resolvedDetail.url || ""))) {
      try {
        const imageBase64 = await captureFacebookDetailClip(pageRef);
        if (imageBase64) {
          const ocr = await ocrExtractFacebookPost({
            imageBase64,
            query: String(candidate.query_used || ""),
            url: String(resolvedDetail.url || ""),
            title: "",
            excerpt: String(candidate.excerpt || candidate.text || ""),
          });
          if (ocr.ok && String(ocr.text || "").trim().length >= 80) {
            resolvedText = cleanFacebookDetailBodyText(String(ocr.text || ""));
            usedPhotoOcr = true;
            photoOcrConfidence = String(ocr.confidence || "");
            resolvedDetail = {
              ...resolvedDetail,
              stage: "comment_overlay_photo_ocr",
            };
          }
        }
      } catch {}
    }
    if (resolvedText && !isLikelyWrongResolvedBody(resolvedText, expectedNeedle)) {
      resolvedDetail = {
        ...resolvedDetail,
        stage: String(resolvedDetail.stage || "comment_overlay"),
      };
    } else {
      resolvedText = "";
      if (candidate.raw_url) {
        let fallbackTab = null;
        try {
          fallbackTab = await openFreshResearchTab(debugPort, String(resolvedDetail.url || candidate.raw_url || ""));
          const fallbackPage = fallbackTab.page;
          await withTimeout(waitForFacebookSearchSettled(fallbackPage, {
            minWaitMs: 1200,
            maxWaitMs: 9000,
            pollMs: 700,
            stableRounds: 0,
            scroll: false,
          }), 12000, `inplace-fallback-settle-timeout:${candidate.dom_index || 0}`);
          const surface = await withTimeout(inspectFacebookDetailSurface(fallbackPage), 8000, `inplace-fallback-inspect-timeout:${candidate.dom_index || 0}`);
          resolvedText = [
            cleanFacebookDetailBodyText(String(surface.bodyText || "")),
            ...(Array.isArray(surface.bodyCandidates) ? surface.bodyCandidates.map((value) => cleanFacebookDetailBodyText(String(value || ""))) : []),
            cleanFacebookDetailBodyText(String(surface.dialogText || "")),
          ].find((value) => String(value || "").trim().length >= 80) || "";
          if (surface?.href && /facebook\.com/i.test(String(surface.href || ""))) {
            resolvedDetail = {
              ...resolvedDetail,
              url: String(surface.href || resolvedDetail.url || candidate.raw_url || ""),
              stage: "fallback_tab",
              dialog_text: String(surface.dialogText || ""),
              body_text: String(surface.bodyText || ""),
              body_candidates: Array.isArray(surface.bodyCandidates) ? surface.bodyCandidates.slice(0, 5) : [],
            };
          }
        } catch {
          resolvedText = "";
        } finally {
          try {
            if (fallbackTab?.page) fallbackTab.page.close();
          } catch {}
          try {
            if (fallbackTab?.targetId) await closeDebugTarget(debugPort, fallbackTab.targetId);
          } catch {}
        }
      }
      if (isLikelyWrongResolvedBody(resolvedText, expectedNeedle)) {
        resolvedDetail = { ok: false, url: "", stage: "resolved_body_mismatch" };
        resolvedText = "";
      }
    }
  }
  const restored = await restoreFacebookSearchTabState(pageRef, sourceState, candidate).catch(() => ({ ok: false }));
  return {
    ...candidate,
    url: String(resolvedDetail?.url || candidate.url || candidate.raw_url || ""),
    raw_url: String(candidate.raw_url || resolvedDetail?.url || candidate.url || ""),
    text: resolvedText || candidate.text || "",
    excerpt: (resolvedText || candidate.text || candidate.excerpt || "").slice(0, 320),
    body_text_clean: resolvedText || "",
    text_source: resolvedText ? (usedPhotoOcr ? "photo_ocr" : "detail_body") : (candidate.text_source || "dom"),
    link_source: String(resolvedDetail?.stage || candidate.link_source || ""),
    resolver_stage: String(resolvedDetail?.stage || ""),
    resolver_ok: Boolean(resolvedDetail?.ok && resolvedText),
    ocr_fallback_used: Boolean(usedPhotoOcr || candidate.ocr_fallback_used),
    ocr_confidence: photoOcrConfidence || candidate.ocr_confidence || "",
    restore_ok: Boolean(restored?.ok),
  };
}

function cleanFacebookDetailBodyText(value = "") {
  let text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  text = text.replace(/\bFacebook(?:\s+Facebook)+\b/gi, " ");
  text = text.replace(/\bAll reactions?:?.*$/i, " ");
  text = text.replace(/\bOnline status indicator\b/gi, " ");
  text = text.replace(/\b[a-z](?:\s+[a-z0-9]){12,}\b/gi, " ");
  const cutPatterns = [
    /\bcomment as\b/i,
    /\bwrite a comment\b/i,
    /\bleave a comment\b/i,
    /\bmost relevant\b/i,
    /\bview more comments\b/i,
    /\bview previous comments\b/i,
    /\bsee more comments\b/i,
    /\ball comments\b/i,
    /\bshared with public\b/i,
    /\bsend message\b/i,
    /\blike reply\b/i,
    /\bpress enter to post\b/i,
    /\bwrite an answer\b/i,
    /\bwhat do you think\?\b/i,
    /\bcommenting has been turned off\b/i,
    /\b\d+\s+comments?\b/i,
    /\b\d+\s+repl(?:y|ies)\b/i,
    /\bAll reactions\b/i,
    /\bOnline status indicator\b/i,
    /\bbình luận với tư cách\b/i,
    /\bviết bình luận\b/i,
    /\bphù hợp nhất\b/i,
    /\bxem thêm bình luận\b/i,
    /\bxem bình luận trước\b/i,
    /\btất cả bình luận\b/i,
    /\btrả lời\b/i,
  ];
  let cutIndex = text.length;
  for (const pattern of cutPatterns) {
    const match = pattern.exec(text);
    if (match && Number.isInteger(match.index) && match.index >= 0) {
      cutIndex = Math.min(cutIndex, match.index);
    }
  }
  text = text.slice(0, cutIndex).trim();
  text = text.replace(/\b(?:Like|Comment|Share|Follow|Reply|Thích|Bình luận|Chia sẻ|Theo dõi|Trả lời)\b/gi, " ");
  text = text.replace(/\bjoin\b/gi, " ");
  text = text.replace(/\s*[·•]\s*/g, " ");
  text = text.replace(/\s+/g, " ").trim();
  return text;
}

async function inspectFacebookDetailSurface(page) {
  const detailResult = await page.call("Runtime.evaluate", {
    expression: `
(() => {
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const cleanDetailText = (value) => {
    let text = normalize(value || '');
    if (!text) return '';
    const cutPatterns = [
      /\\bcomment as\\b/i,
      /\\bwrite a comment\\b/i,
      /\\bleave a comment\\b/i,
      /\\bmost relevant\\b/i,
      /\\bview more comments\\b/i,
      /\\bview previous comments\\b/i,
      /\\bsee more comments\\b/i,
      /\\ball comments\\b/i,
      /\\bshared with public\\b/i,
      /\\bsend message\\b/i,
      /\\blike reply\\b/i,
      /\\bpress enter to post\\b/i,
      /\\bwrite an answer\\b/i,
      /\\bwhat do you think\\?\\b/i,
      /\\bcommenting has been turned off\\b/i,
      /\\b\\d+\\s+comments?\\b/i,
      /\\b\\d+\\s+repl(?:y|ies)\\b/i,
      /\\bbình luận với tư cách\\b/i,
      /\\bviết bình luận\\b/i,
      /\\bphù hợp nhất\\b/i,
      /\\bxem thêm bình luận\\b/i,
      /\\bxem bình luận trước\\b/i,
      /\\btất cả bình luận\\b/i,
      /\\btrả lời\\b/i,
    ];
    let cutIndex = text.length;
    for (const pattern of cutPatterns) {
      const match = pattern.exec(text);
      if (match && Number.isInteger(match.index) && match.index >= 0) {
        cutIndex = Math.min(cutIndex, match.index);
      }
    }
    text = text.slice(0, cutIndex).trim();
    text = text.replace(/\\b(?:Like|Comment|Share|Follow|Reply|Thích|Bình luận|Chia sẻ|Theo dõi|Trả lời)\\b/gi, ' ');
    text = text.replace(/\\bjoin\\b/gi, ' ');
    text = text.replace(/\\bFacebook(?:\\s+Facebook)+\\b/gi, ' ');
    text = text.replace(/\\bAll reactions?:?.*$/i, ' ');
    text = text.replace(/\\bOnline status indicator\\b/gi, ' ');
    text = text.replace(/\\b[a-z](?:\\s+[a-z0-9]){12,}\\b/gi, ' ');
    text = text.replace(/\\s*[·•]\\s*/g, ' ');
    return normalize(text);
  };
  const href = location.href;
  const dialog = document.querySelector('div[role="dialog"]');
  const root = dialog || document.querySelector('div[role="main"]') || document.body;
  const blocks = [
    ...root.querySelectorAll('[data-ad-rendering-role="story_message"], div[data-ad-preview="message"], div[data-ad-comet-preview="message"]'),
    ...root.querySelectorAll('div[dir="auto"], span[dir="auto"]')
  ];
  const bodyCandidates = [];
  const seen = new Set();
  for (const block of blocks) {
    const raw = cleanDetailText(block?.innerText || block?.textContent || '');
    const key = raw.toLowerCase().slice(0, 180);
    if (!raw || raw.length < 40 || seen.has(key)) continue;
    seen.add(key);
    bodyCandidates.push(raw);
  }
  bodyCandidates.sort((a, b) => b.length - a.length);
  const focusedBody = bodyCandidates[0] || '';
  const dialogText = cleanDetailText(dialog?.innerText || '');
  const bodyText = focusedBody || cleanDetailText(root?.innerText || document.body?.innerText || '');
  return {
    href,
    title: document.title || '',
    dialogText: dialogText.slice(0, 4000),
    bodyText: bodyText.slice(0, 4000),
    bodyCandidates: bodyCandidates.slice(0, 5).map((item) => item.slice(0, 1200)),
    permalinkLike: /facebook\\.com\\/(groups\\/[^/]+\\/permalink\\/|[^/]+\\/posts\\/|permalink\\.php\\?|share\\/p\\/)/i.test(href) || /story_fbid=/i.test(href),
  };
})()
    `,
    returnByValue: true,
    awaitPromise: true,
  });
  return detailResult.result?.value || {};
}

async function captureFacebookDetailClip(page) {
  const clipEval = await page.call("Runtime.evaluate", {
    expression: `
(() => {
  const dialog = document.querySelector('div[role="dialog"]');
  const target = dialog || document.querySelector('div[role="main"]') || document.body;
  const rect = target.getBoundingClientRect();
  const x = Math.max(0, Math.floor(rect.left));
  const y = Math.max(0, Math.floor(rect.top));
  const width = Math.max(320, Math.floor(Math.min(window.innerWidth - x, rect.width)));
  const height = Math.max(220, Math.floor(Math.min(window.innerHeight - y, rect.height)));
  return { x, y, width, height };
})()
    `,
    returnByValue: true,
    awaitPromise: true,
  });
  const clip = clipEval.result?.value || {};
  const width = Number(clip.width || 0);
  const height = Number(clip.height || 0);
  if (width < 200 || height < 120) return "";
  const screenshot = await page.call("Page.captureScreenshot", {
    format: "png",
    clip: {
      x: Number(clip.x || 0),
      y: Number(clip.y || 0),
      width,
      height,
      scale: 1,
    },
    captureBeyondViewport: true,
  });
  return String(screenshot.data || "");
}

async function resolveFacebookPrimaryPermalink(page, {
  domIndex = 0,
  expectedText = "",
}) {
  const clickResult = await page.call("Runtime.evaluate", {
    expression: `
(() => {
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const normalizeLoose = (value) => normalize(value).toLowerCase();
  const permalinkLike = (href) => {
    const value = String(href || '');
    if (!value || !/facebook\\.com/i.test(value)) return false;
    return /\\/posts\\//i.test(value) || /\\/permalink\\//i.test(value) || /story_fbid=/i.test(value) || /permalink\\.php\\?/i.test(value) || /\\/share\\/p\\//i.test(value);
  };
  const searchWrapperHref = (href) => {
    const value = String(href || '');
    return !value || value.includes('/search/posts/') || value.includes('/search/top') || value.includes('__cft__') || value.includes('__tn__=');
  };
  const storyNodes = [...document.querySelectorAll('[data-ad-rendering-role="story_message"], div[data-ad-preview="message"], div[data-ad-comet-preview="message"]')];
  const uniq = [];
  const seen = new Set();
  for (const node of storyNodes) {
    const text = normalize(node.innerText || '');
    const key = text.slice(0, 180).toLowerCase();
    if (!text || seen.has(key)) continue;
    seen.add(key);
    uniq.push(node);
  }
  const expectedNeedle = normalizeLoose(${JSON.stringify(String(expectedText || "").replace(/\s+/g, " ").trim())} || '').slice(0, 120);
  let story = uniq[${Math.max(0, Number(domIndex || 0))}] || null;
  if (expectedNeedle) {
    const matched = uniq.find((node) => normalizeLoose(node.innerText || '').includes(expectedNeedle));
    if (matched) story = matched;
  }
  if (!story) return { ok:false, reason:'no-story-node' };
  let current = story;
  let context = story;
  let hops = 0;
  while (current && hops < 8) {
    const text = normalize(current.innerText || '');
    const links = current.querySelectorAll ? current.querySelectorAll('a[href]').length : 0;
    if (text.length > 60 && links >= 2) {
      context = current;
      break;
    }
    current = current.parentElement;
    hops += 1;
  }
  const anchors = [...context.querySelectorAll('a[href], [role="link"][href]')];
  const visible = (el) => {
    const rect = el.getBoundingClientRect?.();
    if (!rect) return false;
    if (rect.width < 8 || rect.height < 8) return false;
    if (rect.bottom < -20 || rect.top > window.innerHeight + 20) return false;
    return true;
  };
  const anchorScore = (el) => {
    const href = String(el.href || el.getAttribute?.('href') || '');
    const text = normalize(el.textContent || el.innerText || el.getAttribute?.('aria-label') || '');
    let score = 0;
    if (permalinkLike(href)) score += 10;
    if (/^(\\d+\\s*(phut|ph|mins|min|gio|hour|hours|ngay|day|days|tuan|week|weeks|thang|month|months)|yesterday|hom qua|today|hom nay)$/i.test(text)) score += 8;
    if (el.querySelector && (el.querySelector('abbr') || el.querySelector('time'))) score += 6;
    if (/^(see more|xem thêm|comment|bình luận|share|chia sẻ|like|thích)$/i.test(text)) score -= 8;
    if (searchWrapperHref(href)) score -= 5;
    if (/facebook\.com\/groups\/[^/]+\/?$/i.test(href)) score -= 4;
    return score;
  };
  const direct = anchors.find((anchor) => visible(anchor) && permalinkLike(anchor.href || anchor.getAttribute?.('href') || ''));
  if (direct) {
    return {
      ok: true,
      mode: 'direct_href',
      url: String(direct.href || direct.getAttribute?.('href') || ''),
      label: normalize(direct.textContent || direct.innerText || direct.getAttribute?.('aria-label') || '').slice(0, 120),
    };
  }
  const candidate = anchors
    .filter((anchor) => visible(anchor))
    .map((anchor) => ({ anchor, score: anchorScore(anchor) }))
    .sort((left, right) => right.score - left.score)[0] || null;
  if (!candidate || candidate.score < 1) {
    return { ok:false, reason:'no-detail-anchor' };
  }
  candidate.anchor.click();
  return {
    ok:true,
    mode:'clicked_anchor',
    score:candidate.score,
    label: normalize(candidate.anchor.textContent || candidate.anchor.innerText || candidate.anchor.getAttribute?.('aria-label') || '').slice(0, 120),
  };
})()
    `,
    returnByValue: true,
    awaitPromise: true,
  });
  const clickValue = clickResult.result?.value || {};
  if (!clickValue.ok) return { ok: false, stage: "primary_link", ...clickValue };
  if (clickValue.mode === "direct_href" && isFacebookPermalinkLike(clickValue.url)) {
    return {
      ok: true,
      url: String(clickValue.url || ""),
      stage: "direct_href",
      dialog_text: "",
      body_text: "",
    };
  }
  await sleep(1800);
  const detailValue = await inspectFacebookDetailSurface(page);
  const href = String(detailValue.href || "").trim();
  if (!href || !/facebook\.com/i.test(href)) {
    return { ok: false, stage: "primary_link_target", reason: "no-facebook-href", href };
  }
  if (!detailValue.permalinkLike) {
    return {
      ok: false,
      stage: "primary_link_target",
      reason: "not-permalink",
      href,
      dialogText: String(detailValue.dialogText || "").slice(0, 320),
    };
  }
  return {
    ok: true,
    url: href,
    stage: "clicked_anchor",
    dialog_text: String(detailValue.dialogText || "").slice(0, 2400),
    body_text: String(detailValue.bodyText || "").slice(0, 2400),
    body_candidates: Array.isArray(detailValue.bodyCandidates) ? detailValue.bodyCandidates.slice(0, 5) : [],
  };
}

async function resolveFacebookCommentPermalink(page, {
  domIndex = 0,
  browser = "brave",
  expectedText = "",
}) {
  const clickResult = await page.call("Runtime.evaluate", {
    expression: `
(() => {
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const normalizeLoose = (value) => normalize(value).toLowerCase();
  const storyNodes = [...document.querySelectorAll('[data-ad-rendering-role="story_message"], div[data-ad-preview="message"], div[data-ad-comet-preview="message"]')];
  const uniq = [];
  const seen = new Set();
  for (const node of storyNodes) {
    const text = normalize(node.innerText || '');
    const key = text.slice(0, 180).toLowerCase();
    if (!text || seen.has(key)) continue;
    seen.add(key);
    uniq.push(node);
  }
  const expectedNeedle = normalizeLoose(${JSON.stringify(String(expectedText || "").replace(/\s+/g, " ").trim())} || '').slice(0, 120);
  let story = uniq[${Math.max(0, Number(domIndex || 0))}] || null;
  if (expectedNeedle) {
    const matched = uniq.find((node) => normalizeLoose(node.innerText || '').includes(expectedNeedle));
    if (matched) story = matched;
  }
  if (!story) return { ok:false, reason:'no-story-node' };
  const storyText = normalize(story.innerText || '');
  let current = story;
  let context = story;
  let hops = 0;
  let comment = null;
  while (current && hops < 8) {
    const controls = current.querySelectorAll ? current.querySelectorAll('a[href], div[role="button"], span[role="button"], [aria-label]').length : 0;
    const text = normalize(current.innerText || '');
    const nodes = current.querySelectorAll ? [...current.querySelectorAll('div[role="button"], span[role="button"], a[role="link"], [aria-label]')] : [];
    comment = nodes.find((el) => {
      const text = normalize(el.textContent || el.innerText || '');
      const aria = normalize(el.getAttribute('aria-label') || '');
      return /(^comment$|^bình luận$|^binh luan$|leave a comment|write a comment)/i.test(text)
        || /(^comment$|^bình luận$|^binh luan$|leave a comment|write a comment)/i.test(aria);
    }) || null;
    if (comment && controls >= 4 && text.length > 50) { context = current; break; }
    if (controls >= 8 && text.length > 80) { context = current; }
    current = current.parentElement;
    hops += 1;
  }
  if (!comment) {
    const nodes = [...context.querySelectorAll('div[role="button"], span[role="button"], a[role="link"], [aria-label]')];
    comment = nodes.find((el) => {
      const text = normalize(el.textContent || el.innerText || '');
      const aria = normalize(el.getAttribute('aria-label') || '');
      return /(^comment$|^bình luận$|^binh luan$|leave a comment|write a comment)/i.test(text)
        || /(^comment$|^bình luận$|^binh luan$|leave a comment|write a comment)/i.test(aria);
    }) || null;
  }
  if (!comment) return { ok:false, reason:'no-comment-button', storyText: storyText.slice(0, 220) };
  comment.click();
  return { ok:true, storyText: storyText.slice(0, 220) };
})()
    `,
    returnByValue: true,
    awaitPromise: true,
  });
  const clickValue = clickResult.result?.value || {};
  if (!clickValue.ok) return { ok: false, stage: "comment_button", ...clickValue };
  await sleep(2200);
  const detailValue = await inspectFacebookDetailSurface(page);
  const href = String(detailValue.href || "").trim();
  if (!href || !/facebook\.com/i.test(href)) {
    return { ok: false, stage: "comment_target", reason: "no-facebook-href", href };
  }
  if (!detailValue.permalinkLike) {
    return {
      ok: false,
      stage: "comment_target",
      reason: "not-permalink",
      href,
      dialogText: String(detailValue.dialogText || "").slice(0, 320),
    };
  }
  return {
    ok: true,
    url: href,
    expected_text: String(expectedText || "").slice(0, 220),
    dialog_text: String(detailValue.dialogText || "").slice(0, 2400),
    body_text: String(detailValue.bodyText || "").slice(0, 2400),
    body_candidates: Array.isArray(detailValue.bodyCandidates) ? detailValue.bodyCandidates.slice(0, 5) : [],
  };
}

async function probeFacebookCommentTargets(page, limit = 5) {
  const probeResult = await page.call("Runtime.evaluate", {
    expression: `
(() => {
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const storyNodes = [...document.querySelectorAll('[data-ad-rendering-role="story_message"], div[data-ad-preview="message"], div[data-ad-comet-preview="message"]')];
  const uniq = [];
  const seen = new Set();
  for (const node of storyNodes) {
    const text = normalize(node.innerText || '');
    const key = text.slice(0, 140).toLowerCase();
    if (!text || seen.has(key)) continue;
    seen.add(key);
    uniq.push(node);
  }
  return uniq.slice(0, ${Math.max(1, Number(limit || 5))}).map((story, index) => {
    let current = story;
    let context = story;
    let hops = 0;
    let comment = null;
    while (current && hops < 8) {
      const nodes = current.querySelectorAll ? [...current.querySelectorAll('div[role="button"], span[role="button"], a[role="link"], [aria-label], a[href]')] : [];
      comment = nodes.find((el) => {
        const text = normalize(el.textContent || el.innerText || '');
        const aria = normalize(el.getAttribute('aria-label') || '');
        return /(^comment$|^bình luận$|^binh luan$|leave a comment|write a comment)/i.test(text)
          || /(^comment$|^bình luận$|^binh luan$|leave a comment|write a comment)/i.test(aria);
      }) || null;
      if (comment) { context = current; break; }
      current = current.parentElement;
      hops += 1;
    }
    const controls = [...context.querySelectorAll('a[href], div[role="button"], span[role="button"], a[role="link"], [aria-label]')].slice(0, 30);
    const closestAnchor = comment?.closest ? comment.closest('a[href]') : null;
    const permalinkAnchor = controls.find((el) => {
      const href = String(el.href || el.getAttribute?.('href') || '');
      return /facebook\\.com/i.test(href) && (/\\/posts\\//i.test(href) || /\\/permalink\\//i.test(href) || /story_fbid=/i.test(href) || /permalink\\.php\\?/i.test(href) || /\\/share\\/p\\//i.test(href));
    }) || null;
    return {
      dom_index: index,
      story_preview: normalize(story.innerText || '').slice(0, 220),
      comment_found: !!comment,
      comment_tag: comment?.tagName || '',
      comment_role: comment?.getAttribute?.('role') || '',
      comment_text: normalize(comment?.textContent || comment?.innerText || ''),
      comment_aria: normalize(comment?.getAttribute?.('aria-label') || ''),
      comment_href: String(comment?.href || comment?.getAttribute?.('href') || ''),
      closest_anchor_href: String(closestAnchor?.href || ''),
      permalink_anchor_href: String(permalinkAnchor?.href || ''),
      nearby_anchor_count: controls.filter((el) => el.tagName === 'A' && (el.href || el.getAttribute?.('href'))).length,
      outer_html: String(comment?.outerHTML || '').slice(0, 500),
    };
  });
})()
    `,
    returnByValue: true,
    awaitPromise: true,
  });
  return Array.isArray(probeResult.result?.value) ? probeResult.result.value : [];
}

async function probeFacebookCommentOpenInNewTab(debugPort, {
  browser = "brave",
  targetId = "",
  domIndex = 0,
  expectedText = "",
} = {}) {
  const connected = await connectExistingResearchTab(debugPort, String(targetId || ""));
  let page = connected.page;
  let permalinkTab = null;
  try {
    const resolved = await withTimeout(resolveFacebookCommentPermalink(page, {
      domIndex: Number(domIndex || 0),
      browser,
      expectedText: String(expectedText || ""),
    }), 15000, `comment-open-probe:${targetId}:${domIndex}`);
    if (!resolved?.ok || !resolved?.url) {
      return { ok: false, stage: String(resolved?.stage || ""), error: String(resolved?.reason || "comment-open-failed"), resolved };
    }
    permalinkTab = await openFreshResearchTab(debugPort, String(resolved.url || ""));
    const permalinkPage = permalinkTab.page;
    await withTimeout(waitForFacebookSearchSettled(permalinkPage, {
      minWaitMs: 1400,
      maxWaitMs: 10000,
      pollMs: 900,
      stableRounds: 1,
      scroll: false,
    }), 14000, `comment-open-probe-settle:${domIndex}`);
    const surface = await withTimeout(inspectFacebookDetailSurface(permalinkPage), 8000, `comment-open-probe-inspect:${domIndex}`);
    const bodyCandidates = [
      cleanFacebookDetailBodyText(String(surface.bodyText || "")),
      ...(Array.isArray(surface.bodyCandidates) ? surface.bodyCandidates.map((value) => cleanFacebookDetailBodyText(String(value || ""))) : []),
      cleanFacebookDetailBodyText(String(surface.dialogText || "")),
    ].filter((value) => String(value || "").trim().length >= 40);
    const bodyText = bodyCandidates[0] || "";
    return {
      ok: Boolean(bodyText),
      stage: "comment_new_tab_probe",
      url: String(surface?.href || resolved.url || ""),
      body_text: bodyText,
      body_candidates: bodyCandidates.slice(0, 5),
      title: String(surface?.title || ""),
      permalink_like: Boolean(surface?.permalinkLike),
    };
  } finally {
    try {
      if (permalinkTab?.page) permalinkTab.page.close();
    } catch {}
    try {
      if (permalinkTab?.targetId) await closeDebugTarget(debugPort, permalinkTab.targetId);
    } catch {}
    try {
      if (page) page.close();
    } catch {}
  }
}

async function probeFacebookCommentOverlay(debugPort, {
  browser = "brave",
  targetId = "",
  domIndex = 0,
  expectedText = "",
} = {}) {
  const connected = await connectExistingResearchTab(debugPort, String(targetId || ""));
  let page = connected.page;
  try {
    const resolved = await withTimeout(resolveFacebookCommentPermalink(page, {
      domIndex: Number(domIndex || 0),
      browser,
      expectedText: String(expectedText || ""),
    }), 15000, `comment-overlay-probe:${targetId}:${domIndex}`);
    const bodyCandidates = [
      cleanFacebookDetailBodyText(String(resolved?.body_text || "")),
      ...(Array.isArray(resolved?.body_candidates) ? resolved.body_candidates.map((value) => cleanFacebookDetailBodyText(String(value || ""))) : []),
      cleanFacebookDetailBodyText(String(resolved?.dialog_text || "")),
    ].filter((value) => String(value || "").trim().length >= 40);
    const bodyText = bodyCandidates[0] || "";
    return {
      ok: Boolean(resolved?.ok && resolved?.url && bodyText),
      stage: "comment_overlay_probe",
      url: String(resolved?.url || ""),
      body_text: bodyText,
      body_candidates: bodyCandidates.slice(0, 5),
      resolved_ok: Boolean(resolved?.ok),
      link_stage: String(resolved?.stage || ""),
    };
  } finally {
    try {
      await withTimeout(returnFromFacebookDetail(page, ""), 5000, `overlay-probe-cleanup:${targetId}:${domIndex}`);
    } catch {}
    try {
      if (page) page.close();
    } catch {}
  }
}

async function resolveFacebookCopyLink(page, {
  domIndex = 0,
  browser = "brave",
  expectedText = "",
}) {
  const sentinel = `__codex_copylink_${Date.now()}__`;
  try {
    await page.call("Page.bringToFront");
  } catch {}
  await focusBrowserWindow(browser);
  await sleep(500);
  await setClipboardText(sentinel);
  const shareResult = await page.call("Runtime.evaluate", {
    expression: `
(() => {
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const storyNodes = [...document.querySelectorAll('[data-ad-rendering-role="story_message"], div[data-ad-preview="message"], div[data-ad-comet-preview="message"]')];
  const story = storyNodes[${Math.max(0, Number(domIndex || 0))}] || null;
  if (!story) return { ok:false, reason:'no-story-node' };
  const storyText = normalize(story.innerText || '');
  let current = story;
  let context = story;
  let hops = 0;
  while (current && hops < 8) {
    const controls = current.querySelectorAll ? current.querySelectorAll('a[href], div[role="button"], span[role="button"], [aria-label]').length : 0;
    const text = normalize(current.innerText || '');
    if (controls >= 6 && text.length > 80) { context = current; break; }
    current = current.parentElement;
    hops += 1;
  }
  const nodes = [...context.querySelectorAll('div[role="button"], span[role="button"], a[role="link"], [aria-label]')];
  const share = nodes.find((el) => {
    const text = normalize(el.textContent || el.innerText || '');
    const aria = normalize(el.getAttribute('aria-label') || '');
    return /(^share$|^chia sẻ$|^chia se$|send this to friends or post it on your profile|gửi nội dung này)/i.test(text)
      || /(^share$|^chia sẻ$|^chia se$|send this to friends or post it on your profile|gửi nội dung này)/i.test(aria);
  });
  if (!share) return { ok:false, reason:'no-share-button', storyText: storyText.slice(0, 220) };
  share.click();
  return { ok:true, storyText: storyText.slice(0, 220) };
})()
    `,
    returnByValue: true,
    awaitPromise: true,
  });
  const shareValue = shareResult.result?.value || {};
  if (!shareValue.ok) return { ok: false, stage: "share", ...shareValue };
  await sleep(1200);
  const copyResult = await page.call("Runtime.evaluate", {
    expression: `
(() => {
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const viewportLeft = Math.max(0, (window.outerWidth - window.innerWidth) / 2);
  const viewportTop = Math.max(0, window.outerHeight - window.innerHeight);
  const buttons = [...document.querySelectorAll('div[role="button"], span[role="button"], a[role="link"]')];
  const copy = buttons.find((el) => /^(copy link|sao chép liên kết|sao chép link|copy)$/i.test(normalize(el.textContent || el.innerText || '')));
  if (!copy) {
    return {
      ok:false,
      reason:'no-copy-link',
      options: buttons.map((el) => normalize(el.textContent || el.innerText || '')).filter(Boolean).slice(0, 20),
    };
  }
  const rect = copy.getBoundingClientRect();
  return {
    ok:true,
    x: rect.left + rect.width / 2,
    y: rect.top + rect.height / 2,
    screenX: (window.screenX || window.screenLeft || 0) + viewportLeft + rect.left + rect.width / 2,
    screenY: (window.screenY || window.screenTop || 0) + viewportTop + rect.top + rect.height / 2,
    label: normalize(copy.textContent || copy.innerText || ''),
    windowMetrics: {
      screenX: window.screenX || window.screenLeft || 0,
      screenY: window.screenY || window.screenTop || 0,
      outerWidth: window.outerWidth || 0,
      outerHeight: window.outerHeight || 0,
      innerWidth: window.innerWidth || 0,
      innerHeight: window.innerHeight || 0,
      viewportLeft,
      viewportTop,
    },
  };
})()
    `,
    returnByValue: true,
    awaitPromise: true,
  });
  const copyValue = copyResult.result?.value || {};
  if (!copyValue.ok) return { ok: false, stage: "copy_button", ...copyValue };
  await focusBrowserWindow(browser);
  await sleep(350);
  await nativeClickAt(copyValue.screenX || copyValue.x, copyValue.screenY || copyValue.y);
  await sleep(1800);
  const copied = await getClipboardText();
  const cleaned = String(copied || "").trim();
  if (!cleaned || cleaned === sentinel) {
    return { ok: false, stage: "clipboard", reason: "clipboard-unchanged", copied: cleaned };
  }
  if (!/facebook\.com/i.test(cleaned)) {
    return { ok: false, stage: "clipboard", reason: "clipboard-not-facebook-link", copied: cleaned };
  }
  return { ok: true, url: cleaned, expected_text: String(expectedText || "").slice(0, 220) };
}

async function resolveFacebookQueuedCandidate(debugPort, browser, candidate = {}) {
  const targetId = String(candidate.source_target_id || "");
  const sourceState = candidate.source_state && typeof candidate.source_state === "object"
    ? candidate.source_state
    : { url: "", scrollY: 0 };
  let pageRef = null;
  try {
    const connected = await withTimeout(
      connectExistingResearchTab(debugPort, targetId),
      12000,
      `connect-existing-tab-timeout:${targetId}`,
    );
    pageRef = connected.page;
    if (sourceState.url) {
      const currentState = await withTimeout(pageRef.call("Runtime.evaluate", {
        expression: "({ href: location.href, scrollY: window.scrollY || window.pageYOffset || 0 })",
        returnByValue: true,
        awaitPromise: true,
      }), 8000, `read-current-state-timeout:${targetId}`);
      const currentValue = currentState.result?.value || {};
      if (!sameFacebookSearchState(String(currentValue.href || ""), sourceState)) {
        await withTimeout(pageRef.call("Page.navigate", { url: String(sourceState.url || "") }), 8000, `navigate-search-timeout:${targetId}`);
        await withTimeout(waitForFacebookSearchSettled(pageRef, {
          minWaitMs: 1400,
          maxWaitMs: 12000,
          pollMs: 900,
          stableRounds: 1,
          scroll: false,
        }), 15000, `settle-search-timeout:${targetId}`);
      }
      const scrollToY = Math.max(
        0,
        Number(candidate?.story_bounds?.y || candidate?.bounds?.y || sourceState.scrollY || 0) - 240,
      );
      try {
        await withTimeout(pageRef.call("Runtime.evaluate", {
          expression: `window.scrollTo(0, ${Math.max(0, Math.round(scrollToY))}); 'ok';`,
          returnByValue: true,
          awaitPromise: true,
        }), 5000, `scroll-search-timeout:${targetId}`);
        await sleep(500);
      } catch {}
    }
    let resolvedDetail = await withTimeout(resolveFacebookCommentPermalink(pageRef, {
      domIndex: Number(candidate.dom_index || 0),
      browser,
      expectedText: String(candidate.excerpt || candidate.text || ""),
    }), 12000, `comment-resolve-timeout:${targetId}:${candidate.dom_index || 0}`);
    if ((!resolvedDetail.ok || !resolvedDetail.url) && candidate.raw_url) {
      resolvedDetail = await withTimeout(resolveFacebookPrimaryPermalink(pageRef, {
        domIndex: Number(candidate.dom_index || 0),
        expectedText: String(candidate.excerpt || candidate.text || ""),
      }), 10000, `primary-link-timeout:${targetId}:${candidate.dom_index || 0}`);
    }
    if ((!resolvedDetail.ok || !resolvedDetail.url) && candidate.raw_url) {
      resolvedDetail = await withTimeout(resolveFacebookCopyLink(pageRef, {
        domIndex: Number(candidate.dom_index || 0),
        browser,
        expectedText: String(candidate.excerpt || candidate.text || ""),
      }), 12000, `copy-link-timeout:${targetId}:${candidate.dom_index || 0}`);
    }
    let resolvedText = "";
    const expectedNeedle = String(candidate.excerpt || candidate.text || "");
    if (resolvedDetail.ok && resolvedDetail.url) {
      resolvedText = [
        cleanFacebookDetailBodyText(String(resolvedDetail.body_text || "")),
        ...(Array.isArray(resolvedDetail.body_candidates) ? resolvedDetail.body_candidates.map((value) => cleanFacebookDetailBodyText(String(value || ""))) : []),
        cleanFacebookDetailBodyText(String(resolvedDetail.dialog_text || "")),
      ].find((value) => String(value || "").trim().length >= 80) || "";
      if (resolvedText && !isLikelyWrongResolvedBody(resolvedText, expectedNeedle)) {
        resolvedDetail = {
          ...resolvedDetail,
          stage: "comment_overlay",
        };
      } else {
        resolvedText = "";
        let permalinkTab = null;
        try {
          permalinkTab = await openFreshResearchTab(debugPort, String(resolvedDetail.url || ""));
          const permalinkPage = permalinkTab.page;
          await withTimeout(waitForFacebookSearchSettled(permalinkPage, {
            minWaitMs: 1400,
            maxWaitMs: 10000,
            pollMs: 900,
            stableRounds: 1,
            scroll: false,
          }), 14000, `detail-new-tab-settle-timeout:${candidate.dom_index || 0}`);
          const surface = await withTimeout(
            inspectFacebookDetailSurface(permalinkPage),
            8000,
            `detail-new-tab-inspect-timeout:${candidate.dom_index || 0}`,
          );
          resolvedText = [
            cleanFacebookDetailBodyText(String(surface.bodyText || "")),
            ...(Array.isArray(surface.bodyCandidates) ? surface.bodyCandidates.map((value) => cleanFacebookDetailBodyText(String(value || ""))) : []),
            cleanFacebookDetailBodyText(String(surface.dialogText || "")),
          ].find((value) => String(value || "").trim().length >= 80) || "";
          if (surface?.href && /facebook\.com/i.test(String(surface.href || ""))) {
            resolvedDetail = {
              ...resolvedDetail,
              url: String(surface.href || resolvedDetail.url || ""),
              stage: "comment_new_tab",
              dialog_text: String(surface.dialogText || ""),
              body_text: String(surface.bodyText || ""),
              body_candidates: Array.isArray(surface.bodyCandidates) ? surface.bodyCandidates.slice(0, 5) : [],
            };
          }
        } catch {
          resolvedText = "";
        } finally {
          try {
            if (permalinkTab?.page) permalinkTab.page.close();
          } catch {}
          try {
            if (permalinkTab?.targetId) await closeDebugTarget(debugPort, permalinkTab.targetId);
          } catch {}
        }
        if (isLikelyWrongResolvedBody(resolvedText, expectedNeedle)) {
          resolvedDetail = { ok: false, url: "", stage: "resolved_body_mismatch" };
          resolvedText = "";
        }
      }
    }
    if ((!resolvedDetail.ok || !resolvedDetail.url) && candidate.raw_url) {
      let fallbackTab = null;
      try {
        fallbackTab = await openFreshResearchTab(debugPort, String(candidate.raw_url || ""));
        const fallbackPage = fallbackTab.page;
        await withTimeout(waitForFacebookSearchSettled(fallbackPage, {
          minWaitMs: 1400,
          maxWaitMs: 10000,
          pollMs: 900,
          stableRounds: 1,
          scroll: false,
        }), 14000, `fallback-settle-timeout:${candidate.dom_index || 0}`);
        const surface = await withTimeout(inspectFacebookDetailSurface(fallbackPage), 8000, `fallback-inspect-timeout:${candidate.dom_index || 0}`);
        if (surface?.href && /facebook\.com/i.test(String(surface.href || ""))) {
          resolvedDetail = {
            ok: true,
            url: String(surface.href || ""),
            stage: "fallback_tab",
            dialog_text: String(surface.dialogText || ""),
            body_text: String(surface.bodyText || ""),
            body_candidates: Array.isArray(surface.bodyCandidates) ? surface.bodyCandidates.slice(0, 5) : [],
          };
          resolvedText = [
            cleanFacebookDetailBodyText(String(surface.bodyText || "")),
            ...(Array.isArray(surface.bodyCandidates) ? surface.bodyCandidates.map((value) => cleanFacebookDetailBodyText(String(value || ""))) : []),
            cleanFacebookDetailBodyText(String(surface.dialogText || "")),
          ].find((value) => String(value || "").trim().length >= 80) || "";
          if (isLikelyWrongResolvedBody(resolvedText, expectedNeedle)) {
            resolvedDetail = { ok: false, url: "", stage: "fallback_body_mismatch" };
            resolvedText = "";
          }
        }
      } catch {
        // best effort fallback only
      } finally {
        try {
          if (fallbackTab?.page) fallbackTab.page.close();
        } catch {}
        try {
          if (fallbackTab?.targetId) await closeDebugTarget(debugPort, fallbackTab.targetId);
        } catch {}
      }
    }
    try {
      await withTimeout(
        restoreFacebookSearchTabState(pageRef, sourceState, candidate),
        18000,
        `restore-search-state-timeout:${targetId}:${candidate.dom_index || 0}`,
      );
    } catch {}
    return {
      ...candidate,
      url: resolvedDetail?.ok && resolvedDetail?.url ? String(resolvedDetail.url || "") : String(candidate.url || ""),
      text: resolvedText || String(candidate.text || ""),
      excerpt: (resolvedText || String(candidate.text || "")).slice(0, 320) || String(candidate.excerpt || ""),
      text_source: resolvedText ? "detail_body" : String(candidate.text_source || "dom"),
      link_source: resolvedDetail?.ok && resolvedDetail?.url
        ? String(resolvedDetail.stage || "resolved_permalink")
        : String(candidate.link_source || "detail_resolver:failed"),
      resolved_ok: Boolean(resolvedDetail?.ok && resolvedDetail?.url),
    };
  } catch (error) {
    return {
      ...candidate,
      link_source: "detail_resolver:error",
      resolved_ok: false,
      resolver_error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    try {
      if (pageRef) pageRef.close();
    } catch {}
  }
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url || "/", `http://${req.headers.host || "127.0.0.1"}`);
    if (req.method === "OPTIONS") return sendJson(res, 200, { ok: true });
    if (req.method === "GET" && url.pathname === "/health") {
      return sendJson(res, 200, { ok: true, service: "host-browser-bridge", port, host });
    }
    if (req.method === "GET" && url.pathname === "/browsers") {
      return sendJson(res, 200, { ok: true, browsers: await listBrowsers() });
    }
    const body = req.method === "POST" ? await readBody(req) : {};
    if (req.method === "POST" && url.pathname === "/open-url") {
      return sendJson(res, 200, { ok: true, result: openUrl(String(body.url || ""), String(body.browser || "")) });
    }
    if (req.method === "POST" && url.pathname === "/open-current") {
      return sendJson(res, 200, { ok: true, result: await openCurrentUrl(String(body.url || ""), String(body.browser || "brave")) });
    }
    if (req.method === "POST" && url.pathname === "/launch-debug") {
      return sendJson(res, 200, {
        ok: true,
        result: launchDebugBrowser({
          browser: String(body.browser || "chrome"),
          url: String(body.url || "about:blank"),
          debugPort: Number(body.port || 9222),
          profileDir: String(body.profile_dir || ""),
        }),
      });
    }
    if (req.method === "POST" && (url.pathname === "/facebook-research" || url.pathname === "/facbook-research")) {
      return sendJson(res, 200, {
        ok: true,
        result: await facebookExtensionHarvest({
          query: String(body.query || ""),
          browser: String(body.browser || "brave"),
          debugPort: Number(body.port || 9222),
          profileDir: String(body.profile_dir || ""),
          scrollRounds: Number(body.scroll_rounds || 4),
        }),
      });
    }
    if (req.method === "POST" && url.pathname === "/facebook-extension-harvest") {
      return sendJson(res, 200, {
        ok: true,
        result: await facebookExtensionHarvest({
          query: String(body.query || ""),
          browser: String(body.browser || "brave"),
          debugPort: Number(body.port || 9222),
          profileDir: String(body.profile_dir || ""),
          scrollRounds: Number(body.scroll_rounds || 4),
        }),
      });
    }
    if (req.method === "POST" && url.pathname === "/facebook-extension-extract") {
      return sendJson(res, 200, saveFacebookExtensionExtract(body));
    }
    if (req.method === "POST" && url.pathname === "/facebook-comment-probe") {
      const targetId = String(body.targetId || "");
      if (!targetId) {
        return sendJson(res, 400, { ok: false, error: "targetId is required" });
      }
      const connected = await connectExistingResearchTab(Number(body.port || 9222), targetId);
      try {
        return sendJson(res, 200, {
          ok: true,
          targetId,
          items: await probeFacebookCommentTargets(connected.page, Number(body.limit || 5)),
        });
      } finally {
        try {
          connected.page.close();
        } catch {}
      }
    }
    if (req.method === "POST" && url.pathname === "/facebook-comment-open-probe") {
      const targetId = String(body.targetId || "");
      if (!targetId) {
        return sendJson(res, 400, { ok: false, error: "targetId is required" });
      }
      return sendJson(res, 200, {
        ok: true,
        result: await probeFacebookCommentOpenInNewTab(Number(body.port || 9222), {
          browser: String(body.browser || "brave"),
          targetId,
          domIndex: Number(body.domIndex || 0),
          expectedText: String(body.expectedText || ""),
        }),
      });
    }
    if (req.method === "POST" && url.pathname === "/facebook-comment-overlay-probe") {
      const targetId = String(body.targetId || "");
      if (!targetId) {
        return sendJson(res, 400, { ok: false, error: "targetId is required" });
      }
      return sendJson(res, 200, {
        ok: true,
        result: await probeFacebookCommentOverlay(Number(body.port || 9222), {
          browser: String(body.browser || "brave"),
          targetId,
          domIndex: Number(body.domIndex || 0),
          expectedText: String(body.expectedText || ""),
        }),
      });
    }
    if (req.method === "POST" && url.pathname === "/screenshot") {
      return sendJson(res, 200, {
        ok: true,
        result: await captureScreenshot({
          pid: Number(body.pid || 0),
          fullScreen: Boolean(body.full_screen),
          name: String(body.name || ""),
          targetDir: String(body.artifact_dir || ""),
          debugPort: Number(body.port || 9222),
        }),
      });
    }
    if (req.method === "POST" && url.pathname === "/mouse-click") {
      return sendJson(res, 200, {
        ok: true,
        result: await nativeClickAt(Number(body.x || 0), Number(body.y || 0)),
      });
    }
    if (req.method === "POST" && url.pathname === "/mouse-move") {
      return sendJson(res, 200, {
        ok: true,
        result: await nativeMoveMouse(Number(body.x || 0), Number(body.y || 0)),
      });
    }
    return sendJson(res, 404, { ok: false, error: "not found", path: url.pathname });
  } catch (error) {
    return sendJson(res, 500, { ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

server.listen(port, host, () => {
  console.log(`Host browser bridge listening on http://${host}:${port}`);
});
