// 터놓고 AI 질문 중계 서버 (Cloudflare Workers)
// - Claude API 키를 숨기고, 터놓고 사이트에서 온 질문만 받는다.
// - 무료는 기기(없으면 IP)마다 한 주에 2번. 유료(하루 5번)는 구조만 있고 아직 아무에게도 주지 않는다.
// - 질문과 답변 내용은 저장하지 않는다. 횟수 세기용으로 기기·IP를 한 방향 암호(해시)로 바꾼 값만 최대 8일 둔다.
// - 방문 통계(/hit): 브라우저마다 하루 한 번, 유입 경로와 기기 종류만 합계용으로 남긴다. 기기 번호·IP는 남기지 않는다.
// - 관리자 화면은 별도 서버(admin-worker/)에 있다. 이 서버는 기록만 남긴다.
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
