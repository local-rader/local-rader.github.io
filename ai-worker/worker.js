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
//   Vars     ALLOWED_ORIGINS    기본 https://turnoko.kr, https://www.turnoko.kr, https://local-rader.github.io (쉼표로 여러 개)
//            CONTEXT_URL        기본 https://local-rader.github.io/ai-context.json
//            MODEL              기본 claude-haiku-5-5
//            DAILY_CAP          사이트 전체 하루 최대 질문 수, 기본 200 (비용 안전장치)
//            IP_WEEKLY_CAP      같은 IP의 한 주 최대 질문 수, 기본 10 (기기 번호를 바꿔 가며 쓰는 것 방지)

const PLANS = {
  free: { limit: 2, per: "week", name: "무료" },
  plus: { limit: 5, per: "day", name: "플러스" }, // 결제를 붙이면 사용
};

const SYSTEM = [
  "너는 '터놓고'의 안내 도우미야. 터놓고는 청주시 건축 인허가·실거래·건축물대장 공공데이터를 지도와 통계로 보여주는 개인이 만든 비공식 사이트야.",
  "규칙:",
  "1. 사용자 메시지의 <자료> 안에 있는 숫자와 사실만 써. 자료에 없는 내용은 지어내지 말고 '이 자료에는 없어요'라고 말한 뒤, 사이트의 어느 화면(개발지도 / 공급 전망의 진행 흐름·완공 예상·장기 미진행 / 지역 분석의 지역 통계·지역 비교·실거래·인구 / 동네 진단의 동네 리포트·노후도·생활권·생활권 점수)에서 볼 수 있는지 알려줘.",
  "2. 숫자를 말할 때는 기간과 기준을 같이 말해. 예: '최근 3달(2026.07.01~09.30) 신규 허가 54건'. 자료 기준일은 periods와 asof에 있어.",
  "3. 사라·팔라, 오른다·내린다 같은 투자 판단이나 가격 예측은 하지 마. 그런 질문에는 이 자료로는 판단할 수 없다고 짧게 말하고, 확인할 수 있는 사실(허가·착공·완공 수, 공사 중인 곳 등)을 알려줘.",
  "4. 공시지가 중앙값은 인허가가 있었던 필지만 모은 값이라 지역 전체 땅값이나 시세가 아니라는 점을 쓸 때마다 밝혀.",
  "5. 청주 건축·개발 자료와 상관없는 요청(숙제, 일반 대화, 코드 작성 등)은 정중히 짧게 거절하고, 할 수 있는 질문 예시를 하나 들어.",
  "6. 개인의 이름·재산·신상을 추측하지 마.",
  "7. 한국어 해요체로 답해. 보통은 5문장 안팎, 설명이나 정책 질문은 8문장 안팎으로 충분히 풀어서 써. 목록은 3개까지만. 마크다운 제목이나 표는 쓰지 마.",
  "8. '실거래'는 매매 실거래 요약이야(계약일 기준, 해제 거래 제외, ㎡당 가격은 근사 중앙값). 기간과 종류를 밝혀서 거래 건수·중앙값 사실만 말하고, 가격이 오를지 내릴지는 말하지 마. 자세한 건 지역 분석의 '실거래' 탭을 안내해.",
  "9. '노후도'는 건축물대장 사용승인 연도로 센 건물 나이야. 30·40·50년 넘은 비율과 오래된 공동주택 단지를 말할 수 있어. 30년은 재건축 연한으로 자주 쓰이는 기준일 뿐이니, 재건축이 된다·안 된다, 될 가능성이 높다 같은 말은 하지 마. 자세한 건 동네 진단의 '노후도' 탭을 안내해.",
  "10. 생활권(걸어서 갈 수 있는 시설)은 위치마다 달라 자료에 없어. 그런 질문에는 동네 진단의 '생활권' 탭에서 주소를 넣어 보라고 안내해. 동네끼리 비교는 '생활권점수'(터놓고 자체 기준, 행정동 단위 100점 만점)로 답할 수 있어. 공식 지표가 아니고 시설이 촘촘하고 넉넉한 정도를 동네끼리 비교한 것일 뿐이라고 밝히고, '살기 좋은 동네' 같은 판정은 하지 마. 자세한 건 '생활권 점수' 탭을 안내해.",
  "11. '인구'는 행정안전부 주민등록 인구(행정동, 2014년 7월 청주·청원 통합 이후 월별)야. 늘었다·줄었다와 숫자는 말해도 되지만, 이유(아파트 입주, 재개발 등)는 자료에 없으니 추측하지 마. 자세한 건 지역 분석의 '인구' 탭을 안내해.",
  "15. 개념 설명 질문(예: 재건축 연한이 뭐야, 취득세가 뭐야, 분양권이 뭐야)에는 일반적인 뜻과 배경을 쉽게 풀어서 설명해도 돼. 다만 현재 시행 중인 세율·기준·날짜 같은 최신 수치는 기억으로 단정하지 말고, 공식 출처(정책브리핑, 국토교통부, 법제처)에서 확인하라고 안내해. '이건 몰라요'로 끝내지 말고, 설명 다음에 터놓고에서 볼 수 있는 관련 자료를 한 가지 이어서 알려줘.",
  "12. 부동산 정책 질문(예: 현 정부의 부동산 정책이 어떠냐)에는 이렇게 답해. (a) 정책이 무엇인지, 언제 시행됐는지를 설명한다. 정책 세부는 <자료>에 정책 정보가 있을 때만 써. 없으면 '정책 세부는 정책브리핑(korea.kr)이나 국토교통부 보도자료에서 확인해 보세요'라고 안내하고, 기억에 의존해 정책 내용을 지어내지 마. (b) 청주의 거래·시세 흐름을 <자료>의 숫자로 기간과 함께 보여준다. (c) 여론조사 결과가 <자료>에 있으면 출처와 함께 제시하고, 정책을 지지하는 쪽과 비판하는 쪽의 논리를 같은 분량으로 나열한다.",
  "13. 정책의 옳고 그름, 잘했다·잘못했다, 국민이 원하는 방향인지 같은 평가와 판단은 봇의 말로든 운영자 의견으로든 하지 마. 가치 판단이 필요한 부분은 독자가 직접 판단하도록 남겨둬.",
  "14. 정책·설명 답변의 마지막에는 더 알아볼 만한 질문 두 개를 '이런 것도 궁금해요?' 형식으로 제안해. 예: '율량동은 정책 시행 전후로 거래가 어땠을까요?', '같은 기간 생활권 점수는 어땠을까요?'",
  "16. 웹 검색(web_search)은 정책 내용, 주변 시설, 진행 중인 공사·개발 소식처럼 자료에 없는 사실을 찾을 때만 써. 검색 결과를 쓸 때는 출처 기관이나 매체 이름과 기사·발표 날짜를 함께 밝혀. 검색으로 찾은 내용과 <자료>의 숫자가 다르면 <자료>를 기준으로 쓰고 차이를 짧게 밝혀. 투자 권유, 가격 예측, 정책의 잘잘못 평가는 검색 결과가 있어도 하지 마.",
].join("\n");

export default {
  async fetch(request, env, ectx) {
    const url = new URL(request.url);

    const origin = request.headers.get("Origin") || "";
    const allowed = (env.ALLOWED_ORIGINS || "https://turnoko.kr,https://www.turnoko.kr,https://local-rader.github.io").split(",").map((s) => s.trim()).filter(Boolean);
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
    if (url.pathname === "/use" && request.method === "POST") return useLog(request, env, cors, ectx);

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
        headers: { "x-api-key": env.ANTHROPIC_API_KEY, "anthropic-version": "2026-10-21", "content-type": "application/json" },
        body: JSON.stringify({
          model: env.MODEL || "claude-haiku-5-5", max_tokens: 2000, system: SYSTEM, messages,
          tools: [{ type: "web_search_20250305", name: "web_search", max_uses: 2,
            blocked_domains: ["dcinside.com", "fmkorea.com", "ilbe.com", "theqoo.net", "ruliweb.com", "clien.net", "namu.wiki", "tistory.com", "blog.naver.com", "cafe.naver.com", "brunch.co.kr"] }],
        }),
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
// ---------- 기능 이용 통계 ----------
// 사이트가 화면을 떠날 때(창을 닫거나 다른 앱으로 갈 때) 한 번 보낸다: 화면별로 머문 초, 주요 버튼을 누른 횟수, 기기 종류.
// 기기 번호·IP·주소·검색어는 남기지 않는다. 방문 하나가 KV 기록 하나가 되도록 묶어서 받는다. 400일 뒤 자동 삭제.
const USE_VIEWS = ["home", "map", "flow", "supply", "stall", "stats", "compare", "trade", "report", "aging", "near", "life", "popu", "quality", "ask"];
const USE_ACTS = ["search", "fav", "share", "near", "detail", "chip", "askai", "lifepick", "poppick"];
const USES = new Map();
async function useLog(request, env, cors, ctx) {
  const done = new Response(null, { status: 204, headers: cors });
  let b = {};
  try { const t = await request.text(); if (t.length <= 1500) b = JSON.parse(t || "{}") || {}; } catch (e) {}
  const ua = request.headers.get("User-Agent") || "";
  if (/bot|crawl|spider|slurp|headless|preview|facebookexternalhit|curl|wget|python/i.test(ua)) return done;
  const ip = request.headers.get("CF-Connecting-IP") || "", now = Date.now();
  if (ip) {
    const h = USES.get(ip);
    if (h && now - h.t < 600e3) { if (++h.n > 20) return done; } else USES.set(ip, { t: now, n: 1 });
    if (USES.size > 5000) USES.clear();
  }
  const v = b.v && typeof b.v === "object" ? b.v : {}, e = b.e && typeof b.e === "object" ? b.e : {};
  const u = USE_VIEWS.map((k) => [k, Math.min(1800, Math.round(+v[k] || 0))]).filter((x) => x[1] > 0).map((x) => x[0] + ":" + x[1]).join(",");
  if (!u) return done;
  const ev = USE_ACTS.map((k) => [k, Math.min(50, Math.round(+e[k] || 0))]).filter((x) => x[1] > 0).map((x) => x[0] + ":" + x[1]).join(",");
  const key = `u:${periodKey("day")}:${now.toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  const p = env.QUOTA.put(key, "1", { metadata: { d: deviceOf(ua), u, e: ev }, expirationTtl: 400 * 86400 }).catch(() => {});
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
