import http from "node:http";
import { spawn } from "node:child_process";
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

fs.mkdirSync(artifactDir, { recursive: true });
fs.mkdirSync(profileRoot, { recursive: true });

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

function findBrowserExe(browser = "chrome") {
  const key = String(browser || "chrome").toLowerCase();
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

async function listBrowsers() {
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
  const args = [
    `--remote-debugging-port=${debugPort}`,
    `--user-data-dir=${actualProfileDir}`,
    "--no-first-run",
    "--new-window",
    url,
  ];
  const child = spawn(exe, args, { detached: true, windowsHide: false });
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

function cleanFacebookDisplayText(value = "", maxLen = 220) {
  const cleaned = String(value || "")
    .replace(/\bFacebook(?:\s+Facebook)+\b/gi, "Facebook")
    .replace(/\b(?:Like|Comment|Share|Join|Thích|Bình luận|Chia sẻ)\b/gi, " ")
    .replace(/\s+/g, " ")
    .trim();
  return cleaned.slice(0, maxLen);
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
    mustIncludeGroups: [],
    wantsRoleMatch: false,
    wantsEntryLevel: false,
    wantsLocation: false,
  };

  if (has("devops", "sre", "platform engineer", "cloud engineer", "sysadmin", "site reliability")) {
    profile.roleTerms = ["devops", "sre", "cloud", "platform", "sysadmin"];
    profile.mustIncludeGroups.push(["devops", "sre", "cloud", "platform", "sysadmin"]);
    profile.wantsRoleMatch = true;
  }
  if (has("intern", "thuc tap", "thuc sinh", "fresher", "junior", "khong can kinh nghiem", "không cần kinh nghiệm", "entry level")) {
    profile.seniorityTerms = ["intern", "thực tập", "thực tập sinh", "fresher", "junior", "không cần kinh nghiệm", "entry level"];
    profile.mustIncludeGroups.push(["intern", "thuc tap", "thuc tap sinh", "fresher"]);
    profile.wantsEntryLevel = true;
  }
  if (has("hcm", "tphcm", "tp hcm", "ho chi minh", "sai gon", "hcmc")) {
    profile.locationTerms = ["hcm", "tphcm", "hồ chí minh", "sài gòn"];
    profile.mustIncludeGroups.push(["hcm", "tphcm", "ho chi minh", "sai gon", "hcmc"]);
    profile.wantsLocation = true;
  }
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
      /data analyst/, /data engineer/, /marketing/, /sales/, /\bhr\b/, /nhan su/, /thu ngan/,
    ]),
  };
}

function buildFacebookQueryPlan(rawQuery = "") {
  const profile = detectIntentProfile(rawQuery);
  const original = sanitizeFacebookSearchQuery(rawQuery);
  const candidates = [original];

  if (profile.roleTerms.length && profile.seniorityTerms.length && profile.locationTerms.length) {
    candidates.push("devops intern hcm");
    candidates.push("tuyen devops intern tphcm");
    candidates.push("thuc tap devops ho chi minh");
    candidates.push("devops fresher hcm");
  } else if (profile.roleTerms.length && profile.seniorityTerms.length) {
    candidates.push("devops intern");
    candidates.push("tuyen devops intern");
    candidates.push("thuc tap devops");
  }

  if (normalizeText(original).includes("tuyen") || normalizeText(original).includes("tuyển")) {
    candidates.push(original.replace(/tuyển/gi, "việc làm").replace(/tuyen/gi, "viec lam"));
  }

  return {
    profile,
    queries: uniqueStrings(candidates).slice(0, 5),
  };
}

async function fetchFacebookSearchPlan({ query, maxQueries = 6 }) {
  if (!query) return buildFacebookQueryPlan(query);
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
    return {
      profile: {
        original_query: String(profile.original_query || query || "").trim(),
        normalized_query: String(profile.normalized_query || normalizeText(query)),
        roleTerms: Array.isArray(profile.roleTerms) ? profile.roleTerms : [],
        seniorityTerms: Array.isArray(profile.seniorityTerms) ? profile.seniorityTerms : [],
        locationTerms: Array.isArray(profile.locationTerms) ? profile.locationTerms : [],
        mustIncludeGroups: Array.isArray(profile.mustIncludeGroups) ? profile.mustIncludeGroups : [],
      },
      queries: uniqueStrings([cleanedBaseQuery, ...queryVariants]).slice(0, Math.max(1, Math.min(10, Number(maxQueries || 6)))),
      planner_intent: String(data.intent || "").trim(),
      planner_criteria: Array.isArray(data.criteria) ? data.criteria.map((item) => String(item || "")).filter(Boolean) : [],
      planner_summary: String(data.summary || "").trim(),
    };
  } catch {
    return buildFacebookQueryPlan(query);
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
  const base = `https://www.facebook.com/search/top?q=${encodeURIComponent(String(rawQuery || "").trim())}`;
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
    score -= 9;
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
    score -= 8;
    reasons.push("wrong-role");
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
    && !reasons.includes("role-mismatch");
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
      items: items.slice(0, 20).map((item) => ({
        url: String(item.url || ""),
        author: String(item.author || ""),
        text: String(item.text || ""),
        excerpt: String(item.excerpt || ""),
        time_hint: String(item.time_hint || ""),
        query_used: String(item.query_used || ""),
        score: Number(item.score || 0),
        reasons: Array.isArray(item.reasons) ? item.reasons.slice(0, 8) : [],
      })),
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

async function fetchJson(url, init = undefined) {
  const response = await fetch(url, init);
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
  try {
    const version = await fetchJson(`http://127.0.0.1:${debugPort}/json/version`);
    return { launched: false, version, profile_dir: profileDir || path.join(profileRoot, `${browser}-debug-${debugPort}`) };
  } catch {
    const launched = launchDebugBrowser({ browser, url, debugPort, profileDir });
    await sleep(2500);
    const version = await fetchJson(`http://127.0.0.1:${debugPort}/json/version`);
    return { launched: true, version, profile_dir: launched.profile_dir };
  }
}

async function openDebugTab(debugPort, url) {
  const endpoint = `http://127.0.0.1:${debugPort}/json/new?${encodeURIComponent(url)}`;
  try {
    return await fetchJson(endpoint, { method: "PUT" });
  } catch {
    return await fetchJson(endpoint);
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
  scrollRounds,
  plan,
  timeWindow,
}) {
  const targetUrl = String(searchQuery || "").startsWith("http")
    ? String(searchQuery)
    : buildFacebookSearchUrl(searchQuery, timeWindow);
  const targetYears = timeWindow ? deriveFacebookFilterYears(timeWindow) : [];
  const researchTab = await openFreshResearchTab(debugPort, targetUrl);
  const page = researchTab.page;
  const collected = [];
  let lastTitle = "";
  let lastUrl = "";
  let needsLogin = false;
  try {
    await page.call("Page.enable");
    await page.call("Page.navigate", { url: targetUrl });
    await waitForFacebookSearchSettled(page, {
      minWaitMs: 4500,
      maxWaitMs: 26000,
      pollMs: 1400,
      stableRounds: 2,
      scroll: false,
    });
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
      await sleep(800);
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
        minWaitMs: 1800,
        maxWaitMs: 12000,
        pollMs: 900,
        stableRounds: 1,
        scroll: false,
      });
    }
    const initialProgress = await readFacebookSearchProgress(page);
    const visibleStoryCount = Number(initialProgress.storyMessageCount || 0);
    const shouldPreScroll = visibleStoryCount < Math.max(Number(limit || 0) + 2, 6);
    if (shouldPreScroll) {
      for (let i = 0; i < scrollRounds; i += 1) {
        await waitForFacebookSearchSettled(page, {
          minWaitMs: 1200,
          maxWaitMs: 9000,
          pollMs: 1200,
          stableRounds: 2,
          scroll: true,
        });
      }
    }
    await sleep(1800);
    await page.call("Runtime.evaluate", {
      expression: `
(() => {
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const candidates = [...document.querySelectorAll('[role="button"], div[role="button"], span[role="button"], a[role="link"], span, div')];
  let clicked = 0;
  for (const el of candidates) {
    if (clicked >= 12) break;
    const text = normalize(el.textContent || el.innerText || '');
    if (!text) continue;
    if (!/^(see more|view more|xem thêm|xem them|hiển thị thêm|hien thi them)$/.test(text)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 20 || rect.height < 12) continue;
    if (rect.bottom < 0 || rect.top > window.innerHeight * 1.5) continue;
    const context = el.closest('[data-ad-rendering-role="story_message"], div[data-ad-preview="message"], div[data-ad-comet-preview="message"]');
    if (!context) continue;
    try {
      el.click();
      clicked += 1;
    } catch {
      // ignore individual button failures
    }
  }
  return { clicked };
})()
      `,
      returnByValue: true,
      awaitPromise: true,
    });
    await sleep(900);
    const debugProgress = await readFacebookSearchProgress(page);
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
    return !value || value.includes('/search/posts/') || value.includes('__cft__') || value.includes('__tn__=');
  };
  const isLikelyPostHref = (href) => {
    const value = String(href || '');
    if (!value || isSearchWrapperHref(value)) return false;
    return (
      value.includes('/posts/') ||
      value.includes('/permalink/') ||
      value.includes('story_fbid=') ||
      (value.includes('/groups/') && value.includes('/posts/')) ||
      value.includes('/share/p/')
    );
  };
  const linkSelector = [
    'a[href*="/posts/"]',
    'a[href*="/permalink/"]',
    'a[href*="story_fbid="]',
    'a[href*="/groups/"][href*="/posts/"]',
    'a[href*="/search/posts/"][href*="__cft__"]',
    'a[href*="/groups/"][href*="__cft__"]',
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
    const directPostLink = anchors.find((anchor) => isLikelyPostHref(anchor.href));
    const fallbackLink =
      node.querySelector('a[href*="/search/posts/"][href*="__cft__"]') ||
      node.querySelector('a[href*="__cft__"][href*="__tn__=%2CO%2CP-R"]') ||
      node.querySelector('a[href*="__cft__"][href*="__tn__=-UC%2CP-R"]') ||
      node.querySelector('a[href*="/groups/"][href*="__cft__"]');
    const link = directPostLink || fallbackLink;
    const href = directPostLink ? directPostLink.href : '';
    const raw_href = link ? link.href : '';
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
      node.querySelector('a[href*="/posts/"] span, a[href*="story_fbid="] span, a[href*="/search/posts/"][href*="__cft__"] span');
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
    items: items.slice(0, ${Math.max(1, Math.min(20, Number(limit || 8)))})
  };
})()
      `,
      returnByValue: true,
      awaitPromise: true,
    });
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
  for (const [index, story] of unique.slice(0, ${Math.max(1, Math.min(20, Number(limit || 8)))}).entries()) {
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
    const rawAnchor = anchors.find((a) => {
      const href = String(a.href || '');
      return href.includes('/search/posts/') || href.includes('__cft__') || href.includes('/groups/');
    });
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
      let scored = scoreFacebookItem(finalItem, plan.profile);
      if (shouldOpenFacebookDetail(finalItem, scored, timeWindow)) {
        let targetState = { url: targetUrl, scrollY: 0 };
        try {
          try {
            const scrollState = await page.call("Runtime.evaluate", {
              expression: "({ href: location.href, scrollY: window.scrollY || window.pageYOffset || 0 })",
              returnByValue: true,
              awaitPromise: true,
            });
            const scrollValue = scrollState.result?.value || {};
            targetState = {
              url: String(scrollValue.href || targetUrl),
              scrollY: Math.max(0, Number(scrollValue.scrollY || 0) || 0),
            };
          } catch {}
          const resolvedByComment = await resolveFacebookCommentPermalink(page, {
            domIndex: Number(item.dom_index || 0),
            browser,
            expectedText: String(finalItem.excerpt || finalItem.text || ""),
          });
          if (resolvedByComment.ok && resolvedByComment.url) {
            const resolvedDialogText = cleanFacebookDetailBodyText(String(resolvedByComment.dialog_text || ""));
            const resolvedBodyText = cleanFacebookDetailBodyText(String(resolvedByComment.body_text || ""));
            const resolvedText = [resolvedDialogText, resolvedBodyText]
              .find((value) => String(value || "").trim().length >= 80)
              || resolvedDialogText
              || resolvedBodyText
              || "";
            finalItem = {
              ...finalItem,
              url: String(resolvedByComment.url || ""),
              link_source: "comment_permalink",
              text: resolvedText || finalItem.text,
              excerpt: (resolvedText || finalItem.text || "").slice(0, 320),
            };
            scored = scoreFacebookItem(finalItem, plan.profile);
          } else {
            finalItem = {
              ...finalItem,
              link_source: resolvedByComment.stage
                ? `comment_permalink:${resolvedByComment.stage}`
                : "comment_permalink:failed",
            };
          }
        } catch {
          finalItem = {
            ...finalItem,
            link_source: "comment_permalink:error",
          };
        } finally {
          try {
            await returnFromFacebookDetail(page, targetState);
          } catch {}
        }
      }
      collected.push({
        ...finalItem,
        query_used: searchQuery,
        score: scored.score,
        reasons: scored.reasons,
        relevant: scored.isRelevant,
        recency_hours: scored.recency_hours,
        time_parse_source: scored.time_parse_source || "",
      });
    }
    return {
      collected,
      debugQuery,
      filterState,
      queryIndex: Number(queryIndex || 0),
      targetId: String(researchTab.targetId || ""),
      lastTitle,
      lastUrl,
      needsLogin,
    };
  } finally {
    try {
      page.close();
    } catch {}
    await closeDebugTarget(debugPort, researchTab.targetId);
  }
}

async function facebookResearch({
  query = "",
  browser = "brave",
  debugPort = 9222,
  limit = 8,
  scrollRounds = 4,
  profileDir = "",
}) {
  const plan = await fetchFacebookSearchPlan({ query, maxQueries: 6 });
  const timeWindow = parseQueryTimeWindow(query);
  if (timeWindow) {
    plan.profile = { ...(plan.profile || {}), timeWindowHours: timeWindow.maxHours };
  }
  const ensured = await ensureDebugBrowser({
    browser,
    url: "https://www.facebook.com/",
    debugPort,
    profileDir,
  });
  const collected = [];
  const debugQueries = [];
  const debugFilterStates = [];
  let lastUrl = "";
  let lastTitle = "";
  let needsLogin = false;
  const queryConcurrency = Math.max(
    1,
    Math.min(
      Number(process.env.FACEBOOK_QUERY_TAB_CONCURRENCY || 5),
      Array.isArray(plan.queries) ? plan.queries.length : 1,
      5,
    ),
  );
  const queryResults = await mapWithConcurrency(plan.queries, queryConcurrency, async (searchQuery, queryIndex) =>
    {
      try {
        return await processFacebookSearchQuery({
          searchQuery,
          queryIndex,
          browser,
          debugPort,
          limit,
          scrollRounds,
          plan,
          timeWindow,
        });
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

  const deduped = [];
  const seen = new Set();
  for (const item of collected.sort((a, b) => Number(b.score || 0) - Number(a.score || 0))) {
    const key = `${item.url || ""}::${normalizeText(item.excerpt || item.text || "").slice(0, 120)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(item);
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
  const bucketed = deduped.map(markTimeBucket);
  const confirmedRecent = bucketed.filter((item) => item.relevant && item.time_bucket === "recent_confirmed");
  const unknownStrong = bucketed.filter((item) => item.relevant && item.time_bucket === "time_unknown");
  const staleConfirmed = bucketed.filter((item) => item.relevant && item.time_bucket === "stale_confirmed");
  const candidatePool = timeWindow ? [...confirmedRecent, ...unknownStrong] : bucketed;
  const filtered = timeWindow ? confirmedRecent : bucketed.filter((item) => item.relevant);
  const heuristicSource = (timeWindow ? candidatePool : (filtered.length ? filtered : bucketed));
  const heuristicItems = selectSoftTargetItems(heuristicSource, cappedLimit);
  const rerankPool = (timeWindow ? candidatePool : (filtered.length ? filtered : bucketed)).slice(0, Math.max(cappedLimit, 12));
  const reranked = await rerankFacebookItems({ query, items: rerankPool, topK: cappedLimit });
  let orderedItems = heuristicItems;
  if (reranked.ok) {
    const rankedIndexes = new Set();
    const ranked = [];
    for (const entry of reranked.ranked_items) {
      const index = Number(entry.index);
      if (!Number.isInteger(index) || index < 0 || index >= rerankPool.length || rankedIndexes.has(index)) continue;
      rankedIndexes.add(index);
      ranked.push({
        ...rerankPool[index],
        llm_score: Number(entry.score || 0),
        llm_verdict: String(entry.verdict || "medium"),
        llm_reason: String(entry.reason || ""),
      });
    }
    for (const [index, item] of rerankPool.entries()) {
      if (rankedIndexes.has(index)) continue;
      ranked.push(item);
    }
    const bucketPriority = { recent_confirmed: 0, time_unknown: 1, stale_confirmed: 2, none: 3 };
    ranked.sort((left, right) => {
      const leftPriority = bucketPriority[left.time_bucket || "none"] ?? 99;
      const rightPriority = bucketPriority[right.time_bucket || "none"] ?? 99;
      if (leftPriority !== rightPriority) return leftPriority - rightPriority;
      const leftScore = Number(left.llm_score || left.score || 0);
      const rightScore = Number(right.llm_score || right.score || 0);
      return rightScore - leftScore;
    });
    orderedItems = selectSoftTargetItems(ranked, cappedLimit);
  }
  const topItems = orderedItems.map((item) => ({
    url: item.url,
    raw_url: item.raw_url || "",
    author: item.author,
    excerpt: item.excerpt,
    text: item.text,
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
    llm_score: Number(item.llm_score || 0),
    llm_verdict: item.llm_verdict || "",
    llm_reason: item.llm_reason || "",
  }));
  const displayResults = topItems.slice(0, 10).map((item, index) => ({
    rank: index + 1,
    author: item.author || `Bài ${index + 1}`,
    summary: cleanFacebookDisplayText(item.excerpt || item.text || "", 220),
    keep_reason: describeKeepReason(item),
    time_status: item.time_bucket || "",
    url: item.url || "",
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
    filtered_count: filtered.length,
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
      rerank_intent: reranked.intent || "",
      rerank_criteria: reranked.criteria || [],
      rerank_summary: reranked.summary || "",
      rerank_applied: Boolean(reranked.ok),
    },
    debug_queries: debugQueries,
    debug_filter_states: debugFilterStates,
    tab_debug_summary: tabDebugSummary,
    results: displayResults,
    items: topItems,
  };
}

async function captureScreenshot({ pid = 0, fullScreen = false, name = "", targetDir = "" }) {
  const actualDir = targetDir || artifactDir;
  fs.mkdirSync(actualDir, { recursive: true });
  const safeName = (name || `host_screenshot_${new Date().toISOString().replace(/[:.]/g, "-")}.png`).replace(/[^a-zA-Z0-9_.-]+/g, "_");
  const finalName = safeName.toLowerCase().endsWith(".png") ? safeName : `${safeName}.png`;
  const outputPath = path.join(actualDir, finalName);
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

function cleanFacebookDetailBodyText(value = "") {
  let text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
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
  text = text.replace(/\s+/g, " ").trim();
  return text;
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
    return normalize(text);
  };
  const href = location.href;
  const dialog = document.querySelector('div[role="dialog"]');
  const dialogText = cleanDetailText(dialog?.innerText || '');
  const bodyText = cleanDetailText(document.body?.innerText || '');
  return {
    href,
    title: document.title || '',
    dialogText: dialogText.slice(0, 4000),
    bodyText: bodyText.slice(0, 4000),
    permalinkLike: /facebook\\.com\\/(groups\\/[^/]+\\/permalink\\/|[^/]+\\/posts\\/|permalink\\.php\\?|share\\/p\\/)/i.test(href),
  };
})()
    `,
    returnByValue: true,
    awaitPromise: true,
  });
  const detailValue = detailResult.result?.value || {};
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
  };
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
    if (req.method === "POST" && url.pathname === "/facebook-research") {
      return sendJson(res, 200, {
        ok: true,
        result: await facebookResearch({
          query: String(body.query || ""),
          browser: String(body.browser || "brave"),
          debugPort: Number(body.port || 9222),
          limit: Number(body.limit || 8),
          scrollRounds: Number(body.scroll_rounds || 4),
          profileDir: String(body.profile_dir || ""),
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
        }),
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
