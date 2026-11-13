// 터놓고 AI 질문 중계 서버 (Cloudflare Workers)
// - Claude API 키를 숨기고, 터놓고 사이트에서 온 질문만 받는다.
// - 무료는 기기(없으면 IP)마다 한 주에 2번. 유료(하루 5번)는 구조만 있고 아직 아무에게도 주지 않는다.
// - 질문과 답변 내용은 저장하지 않는다. 횟수 세기용으로 기기·IP를 한 방향 암호(해시)로 바꾼 값만 최대 8일 둔다.
// - 방문 통계(/hit): 브라우저마다 하루 한 번, 유입 경로와 기기 종류만 합계용으로 남긴다. 기기 번호·IP는 남기지 않는다.
// - 관리자 화면(/admin): 방문자·AI 질문·데이터 상태. ADMIN_HASH(비밀번호의 SHA-256)로 잠근다.
//
// 필요한 설정 (Cloudflare 대시보드 → Worker → Settings)
//   Secrets  ANTHROPIC_API_KEY  Claude API 키 (필수)
//            SALT               아무 긴 문자열 (기기·IP를 해시할 때 섞음, 필수 권장)
//   KV       QUOTA              횟수를 세는 KV 네임스페이스 연결 (필수)
//   Vars     ALLOWED_ORIGINS    기본 https://local-rader.github.io (쉼표로 여러 개)
//            CONTEXT_URL        기본 https://local-rader.github.io/ai-context.json
//            MODEL              기본 claude-haiku-5-5
//            DAILY_CAP          사이트 전체 하루 최대 질문 수, 기본 200 (비용 안전장치)
//            IP_WEEKLY_CAP      같은 IP의 한 주 최대 질문 수, 기본 10 (기기 번호를 바꿔 가며 쓰는 것 방지)

const PLANS = {
  free: { limit: 2, per: "week", name: "무료" },
  plus: { limit: 5, per: "day", name: "플러스" }, // 결제를 붙이면 사용
};

const SYSTEM = [
  "너는 '터놓고'의 안내 도우미야. 터놓고는 청주시 건축 인허가 공공데이터(국토교통부 건축HUB)를 지도와 통계로 보여주는 개인이 만든 비공식 사이트야.",
  "규칙:",
  "1. 사용자 메시지의 <자료> 안에 있는 숫자와 사실만 써. 자료에 없는 내용은 지어내지 말고 '이 자료에는 없어요'라고 말한 뒤, 사이트의 어느 화면(개발지도, 공급 예고, 지역 분석의 지역 통계·진행 흐름·지역 비교·장기 미진행)에서 볼 수 있는지 알려줘.",
  "2. 숫자를 말할 때는 기간과 기준을 같이 말해. 예: '최근 3달(2026.07.01~09.30) 신규 허가 54건'. 자료 기준일은 periods와 asof에 있어.",
  "3. 사라·팔라, 오른다·내린다 같은 투자 판단이나 가격 예측은 하지 마. 그런 질문에는 이 자료로는 판단할 수 없다고 짧게 말하고, 확인할 수 있는 사실(허가·착공·완공 수, 공사 중인 곳 등)을 알려줘.",
  "4. 공시지가 중앙값은 인허가가 있었던 필지만 모은 값이라 지역 전체 땅값이나 시세가 아니라는 점을 쓸 때마다 밝혀.",
  "5. 청주 건축·개발 자료와 상관없는 요청(숙제, 일반 대화, 코드 작성 등)은 정중히 짧게 거절하고, 할 수 있는 질문 예시를 하나 들어.",
  "6. 개인의 이름·재산·신상을 추측하지 마.",
  "7. 한국어 해요체로, 5문장 안팎으로 답해. 목록은 3개까지만. 마크다운 제목이나 표는 쓰지 마.",
].join("\n");

export default {
  async fetch(request, env, ectx) {
    const url = new URL(request.url);
    // 관리자 화면은 사이트 주소 확인보다 먼저 처리한다 (비밀번호로 잠금)
    if (url.pathname === "/admin" || url.pathname === "/admin/") {
      return new Response(ADMIN_HTML, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", "x-robots-tag": "noindex", "referrer-policy": "no-referrer", "x-frame-options": "DENY" } });
    }
    if (url.pathname === "/admin/stats") return adminStats(request, env);

    const origin = request.headers.get("Origin") || "";
    const allowed = (env.ALLOWED_ORIGINS || "https://local-rader.github.io").split(",").map((s) => s.trim()).filter(Boolean);
    const ok = allowed.includes(origin);
    const cors = ok ? {
      "Access-Control-Allow-Origin": origin,
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "content-type, x-device",
      "Access-Control-Max-Age": "86400",
      Vary: "Origin",
    } : { Vary: "Origin" };
    const json = (obj, status = 200) => new Response(JSON.stringify(obj), {
      status, headers: { ...cors, "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
    });

    if (request.method === "OPTIONS") return new Response(null, { status: ok ? 204 : 403, headers: cors });
    if (!ok) return json({ error: "origin", message: "터놓고 사이트에서만 쓸 수 있어요." }, 403);
    if (!env.QUOTA) return json({ error: "config", message: "서버 설정이 끝나지 않았어요. (KV)" }, 500);
    if (url.pathname === "/hit" && request.method === "POST") return hit(request, env, cors, ectx);

    const who = await identity(request, env);
    const plan = await planOf(request, env);
    const P = PLANS[plan];
    const pk = periodKey(P.per);
    const userKey = `u:${plan}:${pk}:${who.id}`;
    const used = +(await env.QUOTA.get(userKey)) || 0;
    const quota = { plan, planName: P.name, limit: P.limit, used, remaining: Math.max(0, P.limit - used), per: P.per, resetAt: resetAt(P.per) };

    if (url.pathname === "/quota" && request.method === "GET") return json(quota);
    if (url.pathname !== "/ask" || request.method !== "POST") return json({ error: "not_found" }, 404);

    // ---- 질문 받기
    let body;
    try {
      const text = await request.text();
      if (text.length > 6000) return json({ error: "bad_request", message: "질문이 너무 길어요." }, 400);
      body = JSON.parse(text);
    } catch (e) {
      return json({ error: "bad_request", message: "질문을 읽지 못했어요." }, 400);
    }
    const q = String(body.q || "").replace(/\s+/g, " ").trim().slice(0, 200);
    if (q.length < 2) return json({ error: "bad_request", message: "질문을 적어 주세요." }, 400);

    // ---- 횟수 확인 (기기별 → IP별 → 사이트 전체)
    if (used >= P.limit) { logAI(env, ectx, "limit"); return json({ error: "limit", message: limitMessage(P), ...quota }, 429); }
    const wk = periodKey("week"), day = periodKey("day");
    const ipKey = `ip:${wk}:${who.ip}`, allKey = `all:${day}`;
    const ipUsed = +(await env.QUOTA.get(ipKey)) || 0;
    if (ipUsed >= (+env.IP_WEEKLY_CAP || 10)) return json({ error: "limit", message: "이 인터넷 연결에서 이번 주 질문이 많아 잠시 막아 두었어요. 다음 주 월요일에 다시 열려요.", ...quota }, 429);
    const allUsed = +(await env.QUOTA.get(allKey)) || 0;
    if (allUsed >= (+env.DAILY_CAP || 200)) return json({ error: "busy", message: "오늘 사이트 전체 질문 한도가 찼어요. 내일 다시 시도해 주세요. 질문 버튼은 계속 쓸 수 있어요.", ...quota }, 503);
    if (!env.ANTHROPIC_API_KEY) return json({ error: "config", message: "서버 설정이 끝나지 않았어요. (API 키)" }, 500);

    // ---- 자료 고르기
    let ctx;
    try { ctx = await loadContext(env); } catch (e) {
      return json({ error: "context", message: "자료를 불러오지 못했어요. 잠시 뒤 다시 시도해 주세요." }, 502);
    }
    const place = typeof body.place === "string" ? body.place.slice(0, 30) : "";
    const picked = pickPlaces(ctx, q, place);
    const data = {
      asof: ctx.asof, briefMonth: ctx.briefMonth, periods: ctx.periods, definitions: ctx.definitions,
      rankings: ctx.rankings,
      places: Object.fromEntries([["청주시 전체", ctx.places["청주시 전체"]], ...picked.map((n) => [n, ctx.places[n]])]),
      note: picked.length ? undefined : "질문에서 동네 이름을 찾지 못해 청주시 전체 자료만 넣었어요.",
    };

    // ---- 앞선 대화(최대 2번)를 이어서 보낸다
    const messages = [];
    (Array.isArray(body.history) ? body.history.slice(-2) : []).forEach((h) => {
      const hq = String((h && h.q) || "").slice(0, 200), ha = String((h && h.a) || "").slice(0, 600);
      if (hq && ha) messages.push({ role: "user", content: hq }, { role: "assistant", content: ha });
    });
    messages.push({ role: "user", content: "<자료>\n" + JSON.stringify(data) + "\n</자료>\n\n질문: " + q });

    let answer;
    try {
      const r = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: { "x-api-key": env.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json" },
        body: JSON.stringify({ model: env.MODEL || "claude-haiku-5-5", max_tokens: 2000, system: SYSTEM, messages }),
      });
      if (!r.ok) throw new Error("status " + r.status);
      const out = await r.json();
      answer = (out.content || []).filter((c) => c.type === "text").map((c) => c.text).join("\n").trim();
      if (!answer) throw new Error("empty");
    } catch (e) {
      logAI(env, ectx, "fail");
      return json({ error: "ai", message: "AI가 지금 답하지 못했어요. 횟수는 줄지 않았어요. 잠시 뒤 다시 시도해 주세요.", ...quota }, 502);
    }

    // ---- 답을 받은 경우에만 횟수를 센다 (저장이 잠깐 실패해도 답은 돌려준다)
    try {
      await Promise.all([
        env.QUOTA.put(userKey, String(used + 1), { expirationTtl: P.per === "day" ? 2 * 86400 : 8 * 86400 }),
        env.QUOTA.put(ipKey, String(ipUsed + 1), { expirationTtl: 8 * 86400 }),
        env.QUOTA.put(allKey, String(allUsed + 1), { expirationTtl: 2 * 86400 }),
      ]);
    } catch (e) {}
    logAI(env, ectx, "ok");
    return json({ answer, places: picked, asof: ctx.asof, ...quota, used: used + 1, remaining: Math.max(0, P.limit - used - 1) });
  },
};

// 결제를 붙이면 여기서 구독 여부를 확인해 "plus"를 돌려준다. 지금은 모두 무료.
async function planOf(request, env) { return "free"; }

async function identity(request, env) {
  const salt = env.SALT || "turnoko";
  const ip = request.headers.get("CF-Connecting-IP") || "0.0.0.0";
  const dev = (request.headers.get("X-Device") || "").trim();
  const good = /^[A-Za-z0-9-]{16,64}$/.test(dev);
  return { id: await sha(salt + "|" + (good ? "d:" + dev : "ip:" + ip)), ip: await sha(salt + "|ip:" + ip) };
}
async function sha(s) {
  const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(b)].slice(0, 16).map((x) => x.toString(16).padStart(2, "0")).join("");
}

// 한국 시간 기준: 하루는 0시, 한 주는 월요일 0시에 새로 시작
function kst() { return new Date(Date.now() + 9 * 3600e3); }
function periodKey(per) {
  const d = kst();
  if (per === "day") return d.toISOString().slice(0, 10);
  const mon = new Date(d.getTime() - ((d.getUTCDay() + 6) % 7) * 864e5);
  return "w" + mon.toISOString().slice(0, 10);
}
function resetAt(per) {
  const d = kst();
  const add = per === "day" ? 1 : 7 - ((d.getUTCDay() + 6) % 7);
  const n = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + add));
  return n.toISOString().slice(0, 10) + "T00:00:00+09:00";
}
function limitMessage(P) {
  return P.per === "week" ? `이번 주 무료 AI 질문 ${P.limit}번을 다 썼어요. 다음 주 월요일에 다시 생겨요. 질문 버튼은 횟수 제한 없이 쓸 수 있어요.`
    : `오늘 AI 질문 ${P.limit}번을 다 썼어요. 내일 다시 생겨요.`;
}

// 자료는 한 시간 동안 기억해 두고 다시 쓴다
let CACHE = null;
async function loadContext(env) {
  if (CACHE && Date.now() - CACHE.t < 3600e3) return CACHE.v;
  const r = await fetch(env.CONTEXT_URL || "https://local-rader.github.io/ai-context.json", { cf: { cacheTtl: 3600, cacheEverything: true } });
  if (!r.ok) throw new Error("context " + r.status);
  const v = await r.json();
  CACHE = { t: Date.now(), v };
  return v;
}

// 질문에 나온 동네(최대 3곳)를 찾는다. '율량'처럼 동·읍·면·구를 빼고 써도 찾는다.
// 못 찾으면 사이트에서 골라 둔 동네를 쓴다.
export function pickPlaces(ctx, q, selected) {
  const names = Object.keys(ctx.places || {}).filter((n) => n !== "청주시 전체").sort((a, b) => b.length - a.length);
  const hits = [];
  for (const n of names) {
    const stem = n.replace(/(동|읍|면|구|가)$/, "");
    if (q.includes(n) || (stem.length >= 2 && q.includes(stem))) {
      if (!hits.some((h) => h.includes(n) || n.includes(h))) hits.push(n);
    }
    if (hits.length >= 3) break;
  }
  if (!hits.length && selected && selected !== "all" && ctx.places[selected]) hits.push(selected);
  return hits;
}

// ---------- 방문 통계 ----------
// 사이트가 브라우저마다 하루 한 번 보낸다. 방문마다 새 키 하나를 쓰고(동시에 와도 덮어쓰지 않음),
// 유입 경로(s)와 기기 종류(d)만 메타데이터로 남긴다. 기기 번호·IP·전체 주소는 남기지 않는다. 400일 뒤 자동 삭제.
const HITS = new Map(); // 같은 IP가 짧은 시간에 너무 많이 보내면 이 서버 인스턴스 안에서 거른다
async function hit(request, env, cors, ctx) {
  const done = new Response(null, { status: 204, headers: cors });
  let body = {};
  try { const t = await request.text(); if (t.length <= 500) body = JSON.parse(t || "{}") || {}; } catch (e) {}
  if (body.t === "selftest") return done; // 배포 점검용: 기록하지 않음
  const ua = request.headers.get("User-Agent") || "";
  if (/bot|crawl|spider|slurp|headless|preview|facebookexternalhit|curl|wget|python/i.test(ua)) return done;
  const ip = request.headers.get("CF-Connecting-IP") || "";
  const now = Date.now();
  if (ip) {
    const h = HITS.get(ip);
    if (h && now - h.t < 600e3) { if (++h.n > 30) return done; } else HITS.set(ip, { t: now, n: 1 });
    if (HITS.size > 5000) HITS.clear();
  }
  const meta = { s: sourceOf(String(body.r || "").slice(0, 100), ua), d: deviceOf(ua) };
  const key = `h:${periodKey("day")}:${now.toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  const p = env.QUOTA.put(key, "1", { metadata: meta, expirationTtl: 400 * 86400 }).catch(() => {});
  if (ctx && ctx.waitUntil) ctx.waitUntil(p); else await p;
  return done;
}
export function sourceOf(ref, ua) {
  const r = String(ref || "").toLowerCase();
  if (/instagram/i.test(ua) || r.includes("instagram")) return "instagram";
  if (/KAKAOTALK|KAKAOSTORY/i.test(ua)) return "kakao";
  if (/NAVER\(inapp|NAVER\//i.test(ua)) return "naver";
  if (!r || r === "local-rader.github.io") return "direct";
  if (r.includes("naver")) return "naver";
  if (r.includes("google")) return "google";
  if (r.includes("daum") || r.includes("kakao")) return "kakao";
  if (/threads|facebook|fb\.|^t\.co$|x\.com|twitter|youtube|tiktok|band\.us/.test(r)) return "sns";
  return "other";
}
export function deviceOf(ua) {
  if (/iPhone|iPod|Mobi/i.test(ua)) return "m";
  if (/Android|iPad|Tablet/i.test(ua)) return "t";
  return "d";
}

// AI 질문 결과도 같은 방식으로 남긴다: ok(답함), fail(AI 실패), limit(무료 횟수를 다 쓰고 또 물음)
function logAI(env, ctx, kind) {
  const key = `a:${periodKey("day")}:${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  const p = env.QUOTA.put(key, "1", { metadata: { k: kind }, expirationTtl: 400 * 86400 }).catch(() => {});
  if (ctx && ctx.waitUntil) ctx.waitUntil(p);
}

// ---------- 관리자 ----------
async function shaFull(s) {
  const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");
}
function lastDays(n) {
  const d = kst(), out = [];
  for (let i = n - 1; i >= 0; i--) out.push(new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - i)).toISOString().slice(0, 10));
  return out;
}
async function listAll(kv, prefix, fn) {
  let cursor, pages = 0;
  do {
    const r = await kv.list({ prefix, cursor, limit: 1000 });
    r.keys.forEach((k) => fn(k.name, k.metadata));
    cursor = r.list_complete ? null : r.cursor;
    pages++;
  } while (cursor && pages < 10);
  return !!cursor;
}
async function adminStats(request, env) {
  const headers = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", "x-robots-tag": "noindex" };
  const given = (request.headers.get("X-Admin-Key") || "").trim();
  const want = String(env.ADMIN_HASH || "").trim().toLowerCase();
  if (!want || !given || (await shaFull(given)) !== want) {
    await new Promise((r) => setTimeout(r, 400));
    return new Response(JSON.stringify({ error: "auth" }), { status: 401, headers });
  }
  if (!env.QUOTA) return new Response(JSON.stringify({ error: "config" }), { status: 500, headers });
  const days = lastDays(30);
  const visits = {}, ai = {};
  days.forEach((d) => { visits[d] = { v: 0, s: {}, d: {} }; ai[d] = { ok: 0, fail: 0, limit: 0 }; });
  let truncated = false;
  for (const m of [...new Set(days.map((d) => d.slice(0, 7)))]) {
    truncated = (await listAll(env.QUOTA, `h:${m}`, (k, meta) => {
      const x = visits[k.slice(2, 12)]; if (!x) return;
      const s = (meta && meta.s) || "other", dv = (meta && meta.d) || "d";
      x.v++; x.s[s] = (x.s[s] || 0) + 1; x.d[dv] = (x.d[dv] || 0) + 1;
    })) || truncated;
    truncated = (await listAll(env.QUOTA, `a:${m}`, (k, meta) => {
      const x = ai[k.slice(2, 12)]; if (!x) return;
      const kind = (meta && meta.k) || "ok"; x[kind] = (x[kind] || 0) + 1;
    })) || truncated;
  }
  const today = days[days.length - 1];
  const todayAsked = +(await env.QUOTA.get(`all:${today}`)) || 0;
  let data;
  try { data = await siteMeta(env); } catch (e) { data = { error: String((e && e.message) || e) }; }
  const now = kst().toISOString().slice(0, 16).replace("T", " ");
  return new Response(JSON.stringify({
    now, today, dailyCap: +env.DAILY_CAP || 200, todayAsked, truncated, data,
    days: days.map((d) => ({ day: d, ...visits[d], ai: ai[d] })),
  }), { headers });
}
// 사이트 data.js 맨 앞의 meta(자료 기준일, 마지막 확인 시각, 실패 목록)만 읽는다
async function siteMeta(env) {
  const base = (env.SITE_URL || "https://local-rader.github.io").replace(/\/+$/, "");
  const r = await fetch(base + "/data.js", { cf: { cacheTtl: 300 } });
  if (!r.ok) throw new Error("data.js " + r.status);
  const reader = r.body.getReader(), dec = new TextDecoder();
  let buf = "";
  while (buf.length < 6000) { const { done, value } = await reader.read(); if (done) break; buf += dec.decode(value, { stream: true }); }
  try { await reader.cancel(); } catch (e) {}
  const i = buf.indexOf('"meta":'), j = buf.indexOf(',"places"', i);
  if (i < 0 || j < 0) throw new Error("meta를 찾지 못함");
  const m = JSON.parse(buf.slice(i + 7, j));
  return { asof: m.asof || "", checked: m.checked || "", failed: (m.failed || []).length, unfinished: (m.unfinished || []).length };
}

// ---------- 관리자 화면 (비밀번호는 이 기기 브라우저에만 저장) ----------
const ADMIN_HTML = `<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>터놓고 관리자</title>
<style>
:root{--blue:#4DA8FF;--ink:#222222;--bg:#F8F9FB;--line:#E6E9EF;--mut:#6b717c;--bad:#d64545}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:system-ui,-apple-system,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif;font-size:15px}
header{display:flex;align-items:center;gap:10px;flex-wrap:wrap;max-width:980px;margin:0 auto;padding:18px 16px 6px}
header svg{width:32px;height:32px;color:var(--ink)}
header b{font-size:20px;letter-spacing:-.5px}
#msg{color:var(--mut);font-size:13px;margin-left:auto}
main{max-width:980px;margin:0 auto;padding:0 16px 48px}
.card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:16px 18px;margin-top:12px}
.card h2{font-size:16px;margin:0 0 12px;letter-spacing:-.3px}
.card h2 small{font-weight:400;color:var(--mut);font-size:12px;margin-left:6px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.kpi{background:var(--bg);border-radius:12px;padding:12px}
.kpi small{display:block;color:var(--mut);font-size:12px;margin-bottom:4px}
.kpi b{font-size:26px;letter-spacing:-.5px;font-variant-numeric:tabular-nums}
.bars{display:flex;align-items:flex-end;gap:3px;height:120px;margin-top:16px;border-bottom:1px solid var(--line)}
.bars.sm{height:70px}
.bars i{flex:1;background:var(--blue);border-radius:3px 3px 0 0;min-height:2px}
.bars i:last-child{background:var(--ink)}
.axis{display:flex;justify-content:space-between;color:var(--mut);font-size:11px;margin-top:4px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:0 12px}
.row{display:flex;align-items:center;gap:10px;margin:8px 0;font-size:14px}
.row span{width:120px;flex:none}
.row em{flex:1;height:10px;background:var(--bg);border-radius:5px;overflow:hidden}
.row em i{display:block;height:100%;background:var(--blue);border-radius:5px}
.row b{width:84px;text-align:right;font-weight:600;font-variant-numeric:tabular-nums}
dl{display:grid;grid-template-columns:110px 1fr;gap:8px 12px;margin:0}
dt{color:var(--mut)} dd{margin:0;font-weight:600}
.links{display:flex;flex-wrap:wrap;gap:8px}
.links a,button{display:inline-block;border:1px solid var(--line);background:#fff;color:var(--ink);border-radius:999px;padding:8px 14px;font:inherit;font-size:14px;text-decoration:none;cursor:pointer}
.links a:hover,button:hover{border-color:var(--blue)}
.tools{display:flex;gap:8px;justify-content:flex-end;margin-top:8px}
.mut{color:var(--mut);font-size:13px;line-height:1.6}
.bad{color:var(--bad)!important}
form{display:flex;gap:8px}
input{flex:1;border:1px solid var(--line);border-radius:10px;padding:10px 12px;font:inherit}
form button{background:var(--ink);color:#fff;border-color:var(--ink);border-radius:10px;flex:none;white-space:nowrap;padding:8px 18px}
@media (max-width:640px){.kpis{grid-template-columns:repeat(2,1fr)}.two{grid-template-columns:1fr}.row span{width:96px}}
</style></head><body>
<header>
<svg viewBox="0 0 48 48" aria-hidden="true"><path d="M7 28 L7 21 L24 9 L41 21 L41 28" fill="none" stroke="currentColor" stroke-width="5" stroke-linejoin="round"/><rect x="32" y="5" width="4" height="12" fill="currentColor"/><rect x="20.5" y="18.5" width="3" height="3" rx=".6" fill="currentColor"/><rect x="24.5" y="18.5" width="3" height="3" rx=".6" fill="currentColor"/><rect x="20.5" y="22.5" width="3" height="3" rx=".6" fill="currentColor"/><rect x="24.5" y="22.5" width="3" height="3" rx=".6" fill="currentColor"/><path d="M4 32 L24 27 L44 32 L24 39 Z" fill="#4DA8FF"/></svg>
<b>터놓고 관리자</b><span id="msg"></span>
</header>
<main>
<section id="login" class="card" hidden>
<h2>비밀번호</h2>
<form id="lf"><input id="pw" type="password" autocomplete="current-password" placeholder="관리자 비밀번호" aria-label="관리자 비밀번호"><button>열기</button></form>
<p id="err" class="bad mut"></p>
<p class="mut">한 번 열면 이 브라우저에 기억해요. 다른 사람과 같이 쓰는 기기라면 다 본 뒤 '잠그기'를 누르세요.</p>
</section>
<div id="dash" hidden>
<div class="tools"><button id="re" type="button">새로고침</button><button id="out" type="button">잠그기</button></div>
<section class="card"><h2>방문자<small>브라우저 기준, 하루 한 번만 셈</small></h2>
<div class="kpis"><div class="kpi"><small>오늘</small><b id="v-today">-</b></div><div class="kpi"><small>어제</small><b id="v-yest">-</b></div><div class="kpi"><small>최근 7일</small><b id="v-7">-</b></div><div class="kpi"><small>최근 30일</small><b id="v-30">-</b></div></div>
<div class="bars" id="v-bars" role="img" aria-label="최근 30일 일별 방문자"></div><div class="axis"><span id="v-ax0"></span><span>최근 30일 (검은 막대가 오늘)</span><span id="v-ax1"></span></div>
</section>
<div class="two">
<section class="card"><h2>유입 경로<small>최근 30일</small></h2><div id="src"></div></section>
<section class="card"><h2>기기<small>최근 30일</small></h2><div id="dev"></div></section>
</div>
<section class="card"><h2>AI 질문</h2>
<div class="kpis"><div class="kpi"><small>오늘 답변 / 하루 상한</small><b id="a-today">-</b></div><div class="kpi"><small>최근 7일 답변</small><b id="a-ok">-</b></div><div class="kpi"><small>최근 7일 실패</small><b id="a-fail">-</b></div><div class="kpi"><small>최근 7일 횟수 초과</small><b id="a-lim">-</b></div></div>
<div class="bars sm" id="a-bars" role="img" aria-label="최근 30일 일별 AI 답변 수"></div>
<p class="mut">'횟수 초과'는 무료 2번을 다 쓰고 또 물어보려 한 횟수예요. 유료 플랜 수요를 가늠하는 숫자예요. 비용은 Claude 결제 화면에서 확인하세요.</p>
</section>
<section class="card"><h2>데이터 상태</h2>
<dl><dt>자료 기준일</dt><dd id="d-asof">-</dd><dt>마지막 확인</dt><dd id="d-chk">-</dd><dt>수집 실패</dt><dd id="d-fail">-</dd></dl>
<p id="d-err" class="bad mut"></p>
<p class="mut">국토교통부는 월 단위로 자료를 공개해요. 사이트는 매일 새벽 확인해요. 마지막 확인이 하루 넘게 지났으면 GitHub Actions의 자동 갱신 작업을 확인하세요.</p>
</section>
<section class="card"><h2>바로가기</h2><div class="links">
<a href="https://local-rader.github.io" target="_blank" rel="noopener">터놓고 사이트</a>
<a href="https://github.com/local-rader/local-rader.github.io/actions" target="_blank" rel="noopener">GitHub Actions</a>
<a href="https://console.anthropic.com/settings/billing" target="_blank" rel="noopener">Claude 결제·잔액</a>
<a href="https://dash.cloudflare.com" target="_blank" rel="noopener">Cloudflare</a>
<a href="https://www.instagram.com/turnoko.kr/" target="_blank" rel="noopener">인스타그램</a>
</div></section>
<p class="mut" id="trunc" hidden>기록이 많아 일부만 셌어요.</p>
<p class="mut">숫자는 대략값이에요. 광고 차단 프로그램을 쓰는 방문은 빠지고, 같은 인터넷 연결에서 짧은 시간에 아주 많이 들어오면 일부만 세요. 내 기기를 집계에서 빼려면 그 기기에서 <a href="https://local-rader.github.io/?me=1" target="_blank" rel="noopener">이 링크</a>를 한 번 여세요.</p>
</div>
</main>
<script>
(function () {
  var K = "tk:admin";
  var LBL = { instagram: "인스타그램", naver: "네이버", google: "구글", kakao: "카카오·다음", sns: "기타 SNS", direct: "직접 방문", other: "기타 사이트" };
  var DEV = { m: "휴대폰", t: "태블릿", d: "PC" };
  function $(id) { return document.getElementById(id); }
  function getKey() { try { return localStorage.getItem(K) || ""; } catch (e) { return MEM; } }
  var MEM = "";
  function setKey(v) { MEM = v; try { if (v) localStorage.setItem(K, v); else localStorage.removeItem(K); } catch (e) {} }
  function n(x) { return Number(x || 0).toLocaleString("ko-KR"); }
  function sum(a, f) { return a.reduce(function (s, x) { return s + f(x); }, 0); }
  function show(w) { $("login").hidden = w !== "login"; $("dash").hidden = w !== "dash"; }
  function md(d) { return d.slice(5).replace("-", "."); }
  function bars(el, arr, f) {
    var mx = Math.max(1, Math.max.apply(null, arr.map(f)));
    el.innerHTML = arr.map(function (x) { var v = f(x); return '<i style="height:' + Math.max(2, Math.round(v / mx * 100)) + '%" title="' + md(x.day) + " · " + v + '"></i>'; }).join("");
  }
  function rows(el, obj, lbl) {
    var ks = Object.keys(obj).sort(function (a, b) { return obj[b] - obj[a]; });
    var tot = sum(ks, function (k) { return obj[k]; });
    if (!tot) { el.innerHTML = '<p class="mut">아직 기록이 없어요.</p>'; return; }
    el.innerHTML = ks.map(function (k) {
      var p = Math.round(obj[k] / tot * 100);
      return '<div class="row"><span>' + (lbl[k] || "기타") + '</span><em><i style="width:' + p + '%"></i></em><b>' + n(obj[k]) + " · " + p + "%</b></div>";
    }).join("");
  }
  function draw(j) {
    show("dash");
    var D = j.days, t = D[D.length - 1], y = D[D.length - 2] || { v: 0 }, w = D.slice(-7);
    $("msg").textContent = "기준 " + j.now + " (한국 시간)";
    $("v-today").textContent = n(t.v); $("v-yest").textContent = n(y.v);
    $("v-7").textContent = n(sum(w, function (x) { return x.v; })); $("v-30").textContent = n(sum(D, function (x) { return x.v; }));
    bars($("v-bars"), D, function (x) { return x.v; });
    $("v-ax0").textContent = md(D[0].day); $("v-ax1").textContent = md(t.day);
    var S = {}, V = {};
    D.forEach(function (x) {
      Object.keys(x.s).forEach(function (k) { S[k] = (S[k] || 0) + x.s[k]; });
      Object.keys(x.d).forEach(function (k) { V[k] = (V[k] || 0) + x.d[k]; });
    });
    rows($("src"), S, LBL); rows($("dev"), V, DEV);
    $("a-today").textContent = n(j.todayAsked) + " / " + n(j.dailyCap);
    $("a-ok").textContent = n(sum(w, function (x) { return x.ai.ok; }));
    $("a-fail").textContent = n(sum(w, function (x) { return x.ai.fail; }));
    $("a-lim").textContent = n(sum(w, function (x) { return x.ai.limit; }));
    $("a-fail").className = sum(w, function (x) { return x.ai.fail; }) ? "bad" : "";
    bars($("a-bars"), D, function (x) { return x.ai.ok; });
    var d = j.data || {};
    $("d-err").textContent = d.error ? "자료 상태를 읽지 못했어요: " + d.error : "";
    if (!d.error) {
      var hrs = d.checked ? (Date.parse(j.now.replace(" ", "T") + ":00Z") - Date.parse(d.checked.replace(" ", "T") + ":00Z")) / 36e5 : 999;
      $("d-asof").textContent = d.asof || "-";
      $("d-chk").textContent = (d.checked || "-") + (hrs <= 36 ? " · 정상" : " · 확인 필요");
      $("d-chk").className = hrs <= 36 ? "" : "bad";
      $("d-fail").textContent = (d.failed || 0) + "건" + (d.unfinished ? " · 미완료 " + d.unfinished + "건" : "");
      $("d-fail").className = (d.failed || d.unfinished) ? "bad" : "";
    }
    $("trunc").hidden = !j.truncated;
  }
  function load() {
    var k = getKey();
    if (!k) { show("login"); setTimeout(function () { $("pw").focus(); }, 0); return; }
    $("msg").textContent = "불러오는 중…";
    fetch("/admin/stats", { headers: { "X-Admin-Key": k }, cache: "no-store" }).then(function (r) {
      if (r.status === 401) { setKey(""); show("login"); $("err").textContent = "비밀번호가 맞지 않아요."; $("msg").textContent = ""; return null; }
      if (!r.ok) throw new Error("서버 응답 " + r.status);
      return r.json();
    }).then(function (j) { if (j) draw(j); }).catch(function (e) { $("msg").textContent = "불러오지 못했어요: " + e.message; });
  }
  $("lf").onsubmit = function (e) { e.preventDefault(); var v = $("pw").value.trim(); if (!v) return; setKey(v); $("pw").value = ""; $("err").textContent = ""; load(); };
  $("out").onclick = function () { setKey(""); show("login"); $("msg").textContent = ""; };
  $("re").onclick = load;
  load();
})();
</script>
</body></html>`;
