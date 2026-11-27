"""터놓고 살기 좋은 동네 지수 — 청주·충주 61곳 (기준은 결과를 보기 전에 고정, 2026-11-27)

  python livable_score.py [출력 파일]     (기본: livable-score.json)

6개 분야를 같은 비중(각 ⅙)으로 평균. 항목마다 61곳 안 백분위(0~100, 거리는 짧을수록 높음).
자료가 없는 항목은 중간(50점)으로 계산.
  생활 편의  생활권 점수 그대로
  주거       10년 안에 지은 집 비율 ⅓ · 아파트 매매가 추이(같은 단지끼리 2년 전과 비교) ⅓ · ㎡당 전세 보증금 ⅓
             (거래가 적은 동네는 도시 전체 값 쪽으로 당김)
  환경       도시공원 면적(주민 1인당 ¼ · 동 면적 대비 ¼) · 1만㎡ 이상 공원까지 거리 ½ (묘지공원 제외)
  활력       최근 5년 인구 증감률 ½ · 0~14세 비율 ¼ · 20~44세 비율 ¼
  교통       500m 안 정류소를 지나는 버스 노선 수 60% · KTX역까지 거리 20% · 고속도로 IC까지 거리 20%
  교육       집에서 1㎞ 안 학원·교습소 수(교육청 등록, 입시·보습·외국어) ½ · 초등학교까지 거리 ½
거리·노선 수는 생활권 점수와 같은 '주민 위치'(법정동·리 대표점 + 아파트 단지, 세대 수 가중)에서 잼.
"""
import json
import math
import os
import re
import statistics
import sys

import life

HERE = os.path.dirname(os.path.abspath(__file__))
CITIES = ("청주", "충주")
NEW_FROM = 2016          # 10년 안에 지은 집 = 2016년 이후 사용승인
# 거래가 적은 동네는 우연에 크게 흔들리므로 그 도시 전체 값 쪽으로 당김 (거래 n건 → 동네 값 n/(n+K), 도시 값 K/(n+K))
#  거래가 하나도 없으면 도시 전체 값 = 중간 정도로 계산됨
K_RENT = 20              # 전세 계약 수
K_CPX = 5                # 매매가 추이: 두 기간 모두 거래된 단지 수
BIG_PARK = 10000         # ㎡
BUS_R = 0.5              # ㎞
AXES = {
    "생활 편의": {"life": 1},
    "주거": {"new": 1, "trend": 1, "jeonse": 1},
    "환경": {"park_pc": 1, "park_pa": 1, "park_d": 2},
    "활력": {"growth": 2, "kids": 1, "young": 1},
    "교통": {"bus": 3, "ktx": 1, "ic": 1},
    "교육": {"acad": 1, "elem": 1},   # 학령기 아이 순유입(kidsin)은 입주 시기를 재는 것으로 드러나 뺌(값은 참고로 남김)
}
LOW_IS_GOOD = {"park_d", "ktx", "ic", "elem"}
K_KIDS = 100             # 학령기 아이 순유입: 아이 수가 적은 동네는 도시 값 쪽으로 당김
LABEL = {"life": "생활권 점수", "new": "10년 안에 지은 집 비율", "trend": "아파트 매매가 추이", "jeonse": "㎡당 전세 보증금",
         "park_pc": "주민 1인당 공원 면적", "park_pa": "동 면적 대비 공원 면적", "park_d": "큰 공원까지 거리", "growth": "5년 인구 증감률",
         "kids": "0~14세 비율", "young": "20~44세 비율", "bus": "버스 노선 수", "ktx": "KTX역까지 거리", "ic": "고속도로 IC까지 거리",
         "kidsin": "학령기 아이 순유입(전체 인구 증가 대비)", "elem": "초등학교까지 거리", "acad": "1㎞ 안 학원·교습소 수(입시·보습, 외국어)"}
KX, KY = 111.32 * math.cos(math.radians(36.75)), 110.57


def km(a, b):
    return math.hypot((a[1] - b[1]) * KX, (a[0] - b[0]) * KY)


def pct(vals, low_good=False):
    """None은 빼고 백분위 (같은 값은 가운데 순위)"""
    have = [v for v in vals if v is not None]
    n = len(have)
    out = []
    for v in vals:
        if v is None:
            out.append(None)
            continue
        below = sum(1 for w in have if (w > v if low_good else w < v))
        eq = sum(1 for w in have if w == v)
        out.append((below + (eq - 1) / 2) / (n - 1) * 100 if n > 1 else 50)
    return out


def norm_name(x):
    return re.sub(r"(학원|교습소|어학원|\s|\(.*?\)|[^\w])", "", x or "").lower()


def apt_key(x):
    x = re.sub(r"\(.*?\)", "", x or "")
    x = re.sub(r"(아파트|APT|apt|단지|\s|·|-|\.)", "", x.strip())
    return re.sub(r"(청주|충주|충북|흥덕|상당|서원|청원)", "", x).lower()


def academy_points(AG, places, homes):
    """교육청 등록 학원·교습소(입시·보습, 국제화) 위치.
    ① 상가정보에 같은 이름이 있으면 그 좌표 ② 주소 괄호 속 아파트 단지 이름 → 오픈스트리트맵 단지 위치
    ③ 주소 괄호 속 법정동 → 그 법정동 대표점 ④ 읍·면만 알면 그 읍·면에서 집이 가장 많은 곳.
    마지막 원소는 방법별 개수 (note용)"""
    at0 = {p["place"]: p["at"] for p in AG["places"] if p.get("at")}
    out, cnt = [], {"이름": 0, "단지": 0, "법정동": 0, "읍면": 0, "못 찾음": 0}
    for c, L in loadc("livable.json"):
        rows = (L.get("neis") or {}).get("rows", [])
        E = {}
        for x in (life.load(os.path.join(life.CITY_DIRS[c], "extra.json"), {}) or {}).get("academy", []):
            E.setdefault(norm_name(x[0]), []).append((x[1], x[2]))
        OSM = {}
        for nm, typ, la, lo in (life.load(os.path.join(life.CITY_DIRS[c], "newdata.json"), {}) or {}).get("apts", []):
            if typ in ("apartments", "residential") and len(apt_key(nm)) >= 3:
                OSM.setdefault(apt_key(nm), []).append((la, lo))
        if not rows:   # 교육청 자료가 없으면 상가정보 학원으로
            out += [q for qs in E.values() for q in qs]
            continue
        for name, kind, addr, la, lo in rows:
            if kind.split("|")[1] not in ("입시.검정 및 보습", "국제화"):
                continue
            pt, k = None, None
            if la:
                pt, k = (la, lo), "이름"
            nn = norm_name(name)
            if not pt and len(nn) >= 2 and len(E.get(nn, [])) == 1:
                pt, k = E[nn][0], "이름"
            m = re.search(r"\(([^()]*?)\)\s*$", addr)
            inner = [x.strip() for x in m.group(1).split(",")] if m else []
            if not pt and len(inner) >= 2:
                ak = apt_key(inner[1])
                hit = OSM.get(ak) or next((v for kk, v in OSM.items() if len(ak) >= 4 and (ak in kk or kk in ak)), None)
                if hit:
                    pt, k = (sorted(q[0] for q in hit)[len(hit) // 2], sorted(q[1] for q in hit)[len(hit) // 2]), "단지"
            if not pt and inner and inner[0] in at0:
                pt, k = tuple(at0[inner[0]]), "법정동"
            if not pt:
                mm = re.search(r"\s(\S+[읍면])\s", addr)
                if mm:
                    hs = [h for nm, h in homes.items() if nm == mm.group(1)]
                    if hs and hs[0]:
                        best = max(hs[0], key=lambda h: h[2])
                        pt, k = (best[0], best[1]), "읍면"
            if pt:
                out.append(pt)
            cnt[k or "못 찾음"] += 1
    out.append(cnt)
    return out


def loadc(name):
    return [(c, life.load(os.path.join(life.CITY_DIRS[c], name), {})) for c in CITIES]


def main(out_name):
    ctx = life.build(CITIES, os.environ.get("LIFE_ALL", "life-all.json"))
    U = ctx["units"]
    byname = {u["name"]: u for u in U}
    places, find, AG = ctx["places"], ctx["find"], ctx["aging"]
    homes = {}
    for nm, la, lo, w in ctx["homes"]:
        homes.setdefault(nm, []).append((la, lo, w))
    val = {u["name"]: {} for u in U}
    note = {}

    def hmean(nm, f):
        hs = homes.get(nm) or []
        tot = sum(h[2] for h in hs)
        return sum(h[2] * f((h[0], h[1])) for h in hs) / tot if tot else None

    # ---- 생활 편의
    for u in U:
        val[u["name"]]["life"] = u["score"]

    # ---- 주거: 신축 비율 (단독·다가구는 건물 1채 = 1가구, 아파트는 세대 수)
    tot, new = {}, {}
    for p in AG["places"]:
        nm = places.get(p["place"])
        if not nm:
            continue
        for y, c in (p.get("y", {}).get("house") or {}).items():
            tot[nm] = tot.get(nm, 0) + c
            if int(y) >= NEW_FROM:
                new[nm] = new.get(nm, 0) + c
    for c in AG["complexes"]:
        nm = places.get(c.get("place"))
        if not nm or not c.get("hh"):
            continue
        tot[nm] = tot.get(nm, 0) + c["hh"]
        if (c.get("y") or 0) >= NEW_FROM:
            new[nm] = new.get(nm, 0) + c["hh"]
    for nm in val:
        val[nm]["new"] = new.get(nm, 0) / tot[nm] if tot.get(nm) else None

    # ---- 주거: 매매가 추이 (같은 단지의 ㎡당 가격 중앙값, 처음 12개월 vs 마지막 12개월)
    rows = []
    for c, T in loadc("trades.json"):
        for k, v in (T.get("data") or {}).items():
            if k.startswith("apt|"):
                rows += [(c, r) for r in v]
    months = sorted(set(r[0][:7] for _, r in rows))
    A, B = set(months[:12]), set(months[-13:-1] if months[-1] >= "2026-11" else months[-12:])
    note["trend"] = f"{min(A)}~{max(A)} → {min(B)}~{max(B)}"
    cp = {}
    for c, r in rows:
        if r[9] or r[10] or not r[3]:   # 직거래·지분거래 제외
            continue
        per = "A" if r[0][:7] in A else "B" if r[0][:7] in B else None
        nm = places.get(life.place_key(r[1], c == "충주", places))
        if per and nm:
            cp.setdefault((nm, r[1], r[4]), {"A": [], "B": []})[per].append(r[2] / r[3])
    tr = {}
    for (nm, _, _), d in cp.items():
        if len(d["A"]) >= 2 and len(d["B"]) >= 2:
            tr.setdefault(nm, []).append((math.log(statistics.median(d["B"]) / statistics.median(d["A"])), min(len(d["A"]), len(d["B"]))))
    for nm in val:
        xs = tr.get(nm, [])
        val[nm]["_trend_raw"] = (sum(a * w for a, w in xs) / sum(w for _, w in xs)) if xs else None
        val[nm]["_trend_n"] = len(xs)
    city_of = {u["name"]: ("충주" if u["gu"] == "충주시" else "청주") for u in U}
    for c in CITIES:
        xs_c = [a for nm in val if city_of[nm] == c for a, _ in tr.get(nm, [])]
        m = statistics.median(xs_c) if xs_c else 0.0
        note[f"trend_city_{c}"] = round(math.exp(m) - 1, 4)
        for nm in val:
            if city_of[nm] != c:
                continue
            n, raw = val[nm]["_trend_n"], val[nm]["_trend_raw"]
            lg = (n * raw + K_CPX * m) / (n + K_CPX) if n else m
            val[nm]["trend"] = math.exp(lg) - 1

    # ---- 주거: ㎡당 전세 보증금 (최근 12개월, 월세 없는 계약)
    js = {}
    for c, L in loadc("livable.json"):
        for sg, dong, apt, ym, dep, mon, ar in (L.get("rent") or {}).get("rows", []):
            nm = places.get(life.place_key(dong, c == "충주", places))
            if nm and mon == 0 and ar > 0 and dep > 0:
                js.setdefault(nm, []).append(dep / ar)
    for nm in val:
        xs = js.get(nm, [])
        val[nm]["_jeonse_raw"] = statistics.median(xs) if xs else None
        val[nm]["_jeonse_n"] = len(xs)
    for c in CITIES:
        m = statistics.median([x for nm, xs in js.items() if city_of[nm] == c for x in xs])
        note[f"jeonse_city_{c}"] = round(m, 1)
        for nm in val:
            if city_of[nm] == c:
                n, raw = val[nm]["_jeonse_n"], val[nm]["_jeonse_raw"]
                val[nm]["jeonse"] = (n * raw + K_RENT * m) / (n + K_RENT) if n else m

    # ---- 환경
    parea, big = {}, []
    for c, L in loadc("livable.json"):
        for name, kind, la, lo, ar in (L.get("parks") or {}).get("rows", []):
            if kind == "묘지공원":
                continue
            u = find(la, lo)
            if u:
                parea[u["name"]] = parea.get(u["name"], 0) + ar
            if ar >= BIG_PARK:
                big.append((la, lo))
    for u in U:
        nm = u["name"]
        val[nm]["park_pc"] = parea.get(nm, 0) / max(1, u["pop"])
        val[nm]["park_pa"] = parea.get(nm, 0) / max(0.01, u["area"])
        val[nm]["park_d"] = hmean(nm, lambda h: min(km(h, q) for q in big)) if big else None

    # ---- 활력: 5년 인구 증감
    for c, H in loadc("population-history.json"):
        ms = H.get("months", [])
        if not ms:
            continue
        i1 = len(ms) - 1
        y0 = f"{int(ms[i1][:4]) - 5}{ms[i1][4:]}"
        i0 = ms.index(y0) if y0 in ms else None
        note["growth"] = f"{y0} → {ms[i1]}"
        for nm, d in (H.get("units") or {}).items():
            nm2 = life.MERGE_CJ.get(nm, nm) if c == "충주" else nm
            if nm2 in val and i0 is not None:
                v = val[nm2].setdefault("_pop", [0, 0])
                v[0] += d["pop"][i0] or 0
                v[1] += d["pop"][i1] or 0
    for nm in val:
        p = val[nm].pop("_pop", None)
        val[nm]["growth"] = (p[1] / p[0] - 1) if p and p[0] else None

    # ---- 활력: 연령 (법정동·리별 주민등록 인구 → 행정동)
    code2place = {str(p["code"]): p["place"] for p in AG["places"] if p.get("code")}

    def ages_by_unit(key):
        """{동네: {나이: 인구}, '_tot': 전체}"""
        out = {}
        for c, L in loadc("livable.json"):
            for r in (L.get(key) or {}).get("rows", []):
                if not r.get("리명") and str(r.get("읍면동명", "")).endswith(("읍", "면")):
                    continue
                pl = code2place.get(str(r["법정동코드"])) or ((r["읍면동명"] + " " + r["리명"]) if r.get("리명") else r["읍면동명"])
                nm = places.get(pl)
                if not nm:
                    continue
                a = out.setdefault(nm, {"_tot": 0})
                a["_tot"] += r.get("계") or 0
                for k, v in r.items():
                    m = re.match(r"^만?(\d+)세", k)
                    if m and isinstance(v, (int, float)):
                        a[int(m.group(1))] = a.get(int(m.group(1)), 0) + v
        return out
    ag = {}
    for c, L in loadc("livable.json"):
        for r in (L.get("ages") or {}).get("rows", []):
            if not r.get("리명") and str(r.get("읍면동명", "")).endswith(("읍", "면")):
                continue   # 읍·면 합계 행 (리 행과 겹침)
            pl = code2place.get(str(r["법정동코드"]))
            if not pl:
                pl = (r["읍면동명"] + " " + r["리명"]) if r.get("리명") else r["읍면동명"]
            nm = places.get(pl)
            if not nm:
                continue
            a = ag.setdefault(nm, [0, 0, 0])
            for k, v in r.items():
                m = re.match(r"^(\d+)세", k)
                if not m or not isinstance(v, (int, float)):
                    continue
                age = int(m.group(1))
                if age <= 14:
                    a[1] += v
                if 20 <= age <= 44:
                    a[2] += v
            a[0] += r.get("계") or 0
    for nm in val:
        a = ag.get(nm)
        val[nm]["kids"] = a[1] / a[0] if a and a[0] else None
        val[nm]["young"] = a[2] / a[0] if a and a[0] else None
        val[nm]["_age_pop"] = a[0] if a else 0

    # ---- 교육: 학령기 아이 순유입 = 5년 전 2~10세였던 아이들(지금 7~15세)이 얼마나 늘었나 − 같은 기간 전체 인구 증가율
    #  아이들은 사망이 거의 없어 또래 수 변화 = 이사 온 아이 − 떠난 아이. 새 아파트 입주처럼 모두가 들어오는 효과는 전체 증가율로 뺌
    now_a, old_a = ages_by_unit("ages"), ages_by_unit("ages_5y")
    L0 = loadc("livable.json")[0][1]
    t_now = re.findall(r"(\d{4})\d{4}", (L0.get("ages") or {}).get("title", ""))
    t_old = re.findall(r"(\d{4})\d{4}", (L0.get("ages_5y") or {}).get("title", ""))
    gap = int(t_now[-1]) - int(t_old[-1]) if t_now and t_old else 5
    note["kidsin"] = f"{t_old[-1] if t_old else '?'}년 {7 - gap}~{15 - gap}세 → {t_now[-1] if t_now else '?'}년 7~15세 ({gap}년)"
    raw = {}
    for nm in val:
        a, b = now_a.get(nm), old_a.get(nm)
        if not a or not b:
            continue
        k_now = sum(a.get(x, 0) for x in range(7, 16))
        k_old = sum(b.get(x, 0) for x in range(7 - gap, 16 - gap))
        if k_old and b["_tot"]:
            raw[nm] = (k_now / k_old - 1) - (a["_tot"] / b["_tot"] - 1), k_old
    for c in CITIES:
        xs = [(v, n) for nm, (v, n) in raw.items() if city_of[nm] == c]
        m = sum(v * n for v, n in xs) / sum(n for _, n in xs) if xs else 0
        for nm in val:
            if city_of[nm] == c:
                v, n = raw.get(nm, (m, 0))
                val[nm]["kidsin"] = (n * v + K_KIDS * m) / (n + K_KIDS)
                val[nm]["_kids_old"] = n
    # ---- 교육: 초등학교까지 거리 (걸어서 다닐 수 있나)
    elem = [(x[1], x[2]) for _, F in loadc("facilities.json") for x in (F or {}).get("school", []) if "초등" in x[0]]
    for nm in val:
        val[nm]["elem"] = hmean(nm, lambda h: min(km(h, q) for q in elem)) if elem else None

    # ---- 교육: 집에서 1㎞ 안 입시·교과·외국어 학원 수 (학원가에 가까운가)
    acad, how = academy_points(AG, places, homes), None
    note["acad"] = acad.pop()
    for nm in val:
        def nacad(h):
            return sum(1 for q in acad if abs(q[0] - h[0]) < 0.0095 and abs(q[1] - h[1]) < 0.012 and km(h, q) <= 1.0)
        val[nm]["acad"] = hmean(nm, nacad) if acad else None

    # ---- 교통
    stops, ic, st = [], [], []
    for c, L in loadc("livable.json"):
        R = L.get("routes") or {}
        # '314'와 '314(단)'처럼 괄호만 다른 것은 같은 노선으로 셈
        canon = [re.sub(r"\(.*?\)$", "", x).strip() for x in R.get("routes", [])]
        stops += [(c, la, lo, {canon[i] for i in ix}) for _, la, lo, ix in R.get("rows", []) if isinstance(ix, list)]
        T = L.get("transit") or {}
        # 이름이 도로명뿐인 점(예: '경부고속도로')은 나들목인지 알 수 없어 뺌
        ic += [(la, lo) for nm, la, lo in T.get("ic", []) if not re.search(r"(고속도로|도로)$", nm)]
        st += [(la, lo) for _, la, lo in T.get("station", [])]
    note["bus_stops"] = len(stops)
    for u in U:
        nm = u["name"]
        c = "충주" if u["gu"] == "충주시" else "청주"
        mine = [s for s in stops if s[0] == c]

        def nroutes(h, mine=mine):
            seen = set()
            for _, la, lo, ix in mine:
                if abs(la - h[0]) < 0.006 and abs(lo - h[1]) < 0.007 and km(h, (la, lo)) <= BUS_R:
                    seen |= ix
            return len(seen)
        val[nm]["bus"] = hmean(nm, nroutes) if mine else None
        val[nm]["ktx"] = hmean(nm, lambda h: min(km(h, q) for q in st)) if st else None
        val[nm]["ic"] = hmean(nm, lambda h: min(km(h, q) for q in ic)) if ic else None

    # ---- 점수
    names = [u["name"] for u in U]
    P = {}
    for k in LABEL:
        for nm, p in zip(names, pct([val[nm].get(k) for nm in names], k in LOW_IS_GOOD)):
            P.setdefault(nm, {})[k] = None if p is None else round(p)
    res = []
    for u in U:
        nm = u["name"]
        ax = {}
        for a, items in AXES.items():
            # 그래도 값이 없는 항목은 중간(50점)으로: 자료 없음을 좋음이나 나쁨으로 보지 않음
            ax[a] = round(sum(w * (50 if P[nm][k] is None else P[nm][k]) for k, w in items.items()) / sum(items.values()))
        sc = round(sum(v for v in ax.values() if v is not None) / sum(1 for v in ax.values() if v is not None))
        res.append({"name": nm, "gu": u["gu"], "city": "충주" if u["gu"] == "충주시" else "청주", "pop": u["pop"],
                    "score": sc, "axes": ax, "p": P[nm],
                    "v": {k: (round(x, 4) if isinstance(x, float) else x) for k, x in val[nm].items()}})
    res.sort(key=lambda r: (-r["score"], r["name"]))
    for r in res:
        r["rank"] = 1 + sum(1 for w in res if w["score"] > r["score"])
    out = {"meta": {"name": "터놓고 살기 좋은 동네 지수", "axes": AXES, "labels": LABEL, "note": note,
                    "rules": {"new_from": NEW_FROM, "k_rent": K_RENT, "k_cpx": K_CPX, "big_park": BIG_PARK, "bus_r_km": BUS_R}},
           "units": res}
    with open(out_name, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    print("\n살기 좋은 동네 지수", note)
    for r in res:
        print(f"{r['rank']:>2} {r['score']:>3} {r['city']} {r['name']:<12} {r['axes']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "livable-score.json"))
