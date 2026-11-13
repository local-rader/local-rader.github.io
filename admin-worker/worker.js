// 터놓고 관리자 화면 (Cloudflare Workers, 사이트·AI 서버와 다른 주소)
// - "/"      관리자 화면. 비밀번호를 넣어야 숫자가 보인다.
// - "/stats" 숫자(JSON). X-Admin-Key 헤더의 SHA-256이 ADMIN_HASH와 같아야 준다.
// - AI 서버(ai-worker)가 남긴 방문·AI 기록(KV QUOTA)을 읽기만 한다.
//
// 필요한 설정: KV QUOTA(AI 서버와 같은 turnoko-quota), Vars ADMIN_HASH, DAILY_CAP(선택, 기본 200), SITE_URL(선택)

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== "GET") return new Response("method", { status: 405 });
    if (url.pathname === "/" || url.pathname === "/index.html") {
      return new Response(ADMIN_HTML, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", "x-robots-tag": "noindex, nofollow", "referrer-policy": "no-referrer", "x-frame-options": "DENY" } });
    }
    if (url.pathname === "/stats") return adminStats(request, env);
    if (url.pathname === "/robots.txt") return new Response("User-agent: *\nDisallow: /\n", { headers: { "content-type": "text/plain" } });
    return new Response("not found", { status: 404 });
  },
};

// 한국 시간
function kst() { return new Date(Date.now() + 9 * 3600e3); }

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
    fetch("/stats", { headers: { "X-Admin-Key": k }, cache: "no-store" }).then(function (r) {
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
