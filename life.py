"""터놓고 생활권 점수 → life.json

동네(행정동)마다 생활시설 15가지를 4개 분야(교통·생활 / 의료·안전 / 여가·문화 / 돌봄·교육)로 묶어 얼마나 갖춰져 있는지를 두 가지로 잰다.
  · 면적당: 1㎢ 안에 몇 곳인지 (걸어서 닿기 쉬운지)
  · 인구당: 주민 1천 명당 몇 곳인지 (사람 수에 비해 넉넉한지)
시설마다 두 값을 청주 동네끼리 비교해 백분위(0~100)로 바꾸고, 면적 60%·인구 40%로 합친다.
분야 점수 = 분야 안 시설들의 가중 평균(CATEGORIES), 종합 점수 = 4개 분야 평균.
공식 지표가 아니라 터놓고가 공공데이터로 정한 기준이다.

사용법
  python life.py pop 파일 [파일…]  # 행정안전부 '행정동별 주민등록 인구 및 세대현황'(엑셀·CSV, 여러 달 가능)
                                 #  → population-history.json(월별 누적)과 population.json(가장 최근 달)
  python life.py build           # dongs.json·population.json·busstops.json·pois.json·facilities.json·newdata.json → life.json
"""
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
P = lambda n: os.path.join(HERE, n)

# 한 법정동이 두 행정동으로 나뉜 곳은 하나로 합쳐 본다 (실거래·인허가 자료가 법정동 단위라서)
MERGE = {
    "복대1동": "복대동", "복대2동": "복대동", "내덕1동": "내덕동", "내덕2동": "내덕동",
    "용암1동": "용암동", "용암2동": "용암동", "사직1동": "사직동", "사직2동": "사직동",
    "수곡1동": "수곡동", "수곡2동": "수곡동", "봉명1동": "봉명·송정동", "봉명2·송정동": "봉명·송정동",
}
KINDS = [("bus", "버스정류소"), ("park", "공원"), ("lib", "도서관"), ("market", "전통시장"), ("senior", "경로당"),
         ("school", "학교"), ("hospital", "병원·의원"), ("pharmacy", "약국"), ("mart", "슈퍼·편의점"),
         # newdata.py (2026-11 추가)
         ("parking", "주차장"), ("safety", "119·치안센터"), ("sports", "공공체육시설"), ("culture", "문화시설"),
         ("care", "요양병원"), ("child", "어린이복지시설")]
NEW_KINDS = ("parking", "safety", "sports", "culture", "care", "child")
# 생활권 분야 (4개). 분야끼리 같은 비중, 분야 안 시설도 같은 비중. 자료를 못 받은 시설은 빼고 계산한다
CATEGORIES = {   # 분야 안 시설 가중치 (합 1). 자료가 빠진 시설은 나머지로 다시 나눔
    "교통·생활": {"bus": 0.35, "mart": 0.35, "market": 0.15, "parking": 0.15},
    "의료·안전": {"hospital": 0.40, "pharmacy": 0.35, "safety": 0.25},
    "여가·문화": {"park": 0.40, "lib": 0.25, "sports": 0.20, "culture": 0.15},   # 문화시설은 빠진 곳이 많아 작게
    "돌봄·교육": {"school": 0.40, "child": 0.25, "senior": 0.20, "care": 0.15},
}
W_AREA, W_POP = 0.6, 0.4   # 면적당 60%, 인구당 40% (좁은 곳에 모여 있는 것을 더 높게 봄)


def load(name, default=None):
    try:
        with open(P(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(name, obj):
    tmp = P(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, P(name))


def norm_dong(name):
    """'충청북도 청주시 흥덕구 강서제1동' → ('흥덕구', '강서1동'). 청주 읍면동이 아니면 None"""
    m = re.match(r"충청북도 청주시 (\S+구) (.+?)\s*(\(\d+\))?$", str(name or "").strip())
    if not m:
        return None
    gu, dong = m.group(1), m.group(2).strip().replace(".", "·")
    if not dong or dong.startswith("("):
        return None
    dong = re.sub(r"제(\d)동$", r"\1동", dong)
    if dong == "탑대성동":
        dong = "탑·대성동"
    return gu, dong


def read_pop_file(path):
    """행정안전부 내려받기 파일(엑셀 또는 CSV, 여러 달 가능) → {월: {(코드, 구, 동): (인구, 세대)}}"""
    num = lambda v: int(str(v).replace(",", "").strip() or 0)
    out = {}
    if path.lower().endswith(".csv"):
        import csv, io
        raw = open(path, "rb").read()
        try:
            txt = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            txt = raw.decode("cp949")
        rows = list(csv.reader(io.StringIO(txt)))
        head = rows[0]
        cols = {}
        for i, c in enumerate(head):
            m = re.match(r"(\d{4})년(\d{2})월_(총인구수|총 거주자수|세대수)", c)
            if m:
                cols.setdefault(f"{m.group(1)}-{m.group(2)}", {})["hh" if m.group(3) == "세대수" else "pop"] = i
        for r in rows[1:]:
            nd = norm_dong(r[0])
            code = re.search(r"\((\d{10})\)", r[0])
            if not nd:
                continue
            for mo, c in cols.items():
                out.setdefault(mo, {})[(code.group(1) if code else "",) + nd] = (num(r[c["pop"]]), num(r[c.get("hh", c["pop"])]))
    else:
        import openpyxl
        ws = openpyxl.load_workbook(open(path, "rb")).active  # 확장자가 .xls여도 실제 xlsx면 읽힘
        rows = list(ws.iter_rows(values_only=True))
        months = {}
        for i, c in enumerate(rows[1]):
            m = re.match(r"(\d{4})년\s*(\d{1,2})월", str(c or ""))
            if m:
                months[i] = f"{m.group(1)}-{int(m.group(2)):02d}"
        for r in rows[3:]:
            nd = norm_dong(r[1])
            if not nd:
                continue
            for i, mo in months.items():
                out.setdefault(mo, {})[(str(r[0] or "").strip(),) + nd] = (num(r[i]), num(r[i + 1]))
    return out


def pop(paths):
    """내려받은 인구 파일들을 population-history.json에 합치고(같은 달은 새 파일 값으로), 가장 최근 달로 population.json을 만든다."""
    H = load("population-history.json", {"raw": {}})
    got = {}
    for p in paths:
        for mo, rows in read_pop_file(p).items():
            if sum(v[0] for v in rows.values()) <= 0:
                continue  # 통합 전처럼 값이 비어 있는 달은 건너뛴다
            got[mo] = rows
    if not got:
        sys.exit("청주 읍면동 인구가 들어 있는 달이 없습니다. 지역을 충청북도 → 청주시, 읍면동 단위로 받았는지 확인해 주세요.")
    for mo, rows in got.items():
        H["raw"][mo] = {f"{gu}|{dong}": [pv, hv] for (_, gu, dong), (pv, hv) in rows.items()}
    last = max(got)
    codes = {code: v[0] for (code, gu, dong), v in got[last].items() if re.fullmatch(r"\d{10}", code)}
    if len(codes) >= 30 and last >= (load("population.json", {}) or {}).get("meta", {}).get("month", ""):
        save("population.json", {"meta": {"source": "행정안전부 행정동별 주민등록 인구 및 세대현황", "month": last,
                                          "note": "매월 말일 기준 총 거주자 수"}, "pop": codes})
        print(f"population.json: {last}, 행정동 {len(codes)}곳, 합계 {sum(codes.values()):,}명")
    build_hist(H)


def build_hist(H=None):
    """월별 행정동 인구 → 동네(합친 행정동)별 시계열. 사이트의 '인구' 화면이 읽는다."""
    H = H or load("population-history.json", {"raw": {}})
    months = sorted(m for m in H["raw"] if m >= "2014-07")  # 청주·청원 통합 이후만
    units = {}
    for i, mo in enumerate(months):
        for key, (pv, hv) in H["raw"][mo].items():
            gu, dong = key.split("|")
            nm = MERGE.get(dong, dong)
            u = units.setdefault(nm, {"gu": gu, "pop": [0] * len(months), "hh": [0] * len(months)})
            if pv:
                u["gu"] = gu  # 구가 바뀐 동네(2014년 구 개편)는 가장 최근 구로
            u["pop"][i] += pv
            u["hh"][i] += hv
    H["meta"] = {"source": "행정안전부 행정동별 주민등록 인구 및 세대현황 (매월 말일)", "from": months[0], "to": months[-1],
                 "note": "2014년 7월 청주·청원 통합 이후. 한 법정동이 나뉜 행정동(복대1·2동 등)은 합침"}
    H["months"] = months
    H["units"] = units
    H["city"] = {"pop": [sum(u["pop"][i] for u in units.values()) for i in range(len(months))],
                 "hh": [sum(u["hh"][i] for u in units.values()) for i in range(len(months))]}
    save("population-history.json", H)
    # 사이트가 읽는 가벼운 파일 (원자료 없이 동네별 시계열만)
    save("population-trend.json", {k: H[k] for k in ("meta", "months", "units", "city")})
    c = H["city"]["pop"]
    print(f"population-history.json: {months[0]}~{months[-1]} ({len(months)}달), 동네 {len(units)}곳, 청주 {c[0]:,} → {c[-1]:,}명")


def in_ring(x, y, ring):
    c = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]; xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            c = not c
        j = i
    return c


def ring_km2(ring, lat0):
    kx, ky = 111.32 * math.cos(math.radians(lat0)), 110.57
    s = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i - 1]; x2, y2 = ring[i]
        s += (x1 * kx) * (y2 * ky) - (x2 * kx) * (y1 * ky)
    return abs(s) / 2


def pct_ranks(vals):
    """같은 값은 같은 백분위 (0 = 가장 적음, 100 = 가장 많음). 시설이 하나도 없으면 0."""
    n = len(vals)
    out = []
    for v in vals:
        if v <= 0:
            out.append(0)
            continue
        below = sum(1 for w in vals if w < v)
        eq = sum(1 for w in vals if w == v)
        out.append((below + (eq - 1) / 2) / (n - 1) * 100 if n > 1 else 50)
    return out


def build():
    D = load("dongs.json")
    PO = load("population.json")
    if not D or not PO:
        sys.exit("dongs.json 또는 population.json이 없습니다.")
    units = {}
    for d in D["dongs"]:
        nm = MERGE.get(d["name"], d["name"])
        u = units.setdefault(nm, {"name": nm, "gu": d["gu"], "codes": [], "polys": [], "pop": 0, "area": 0.0})
        u["codes"].append(d["code"])
        u["polys"].extend(d["polys"])
        u["pop"] += PO["pop"].get(d["code"], 0)
    lat0 = 36.64
    for u in units.values():
        u["area"] = sum(ring_km2(p[0], lat0) - sum(ring_km2(h, lat0) for h in p[1:]) for p in u["polys"])
        xs = [pt[0] for p in u["polys"] for pt in p[0]]; ys = [pt[1] for p in u["polys"] for pt in p[0]]
        u["bb"] = [min(xs), min(ys), max(xs), max(ys)]
        u["n"] = {k: 0 for k, _ in KINDS}

    def find(lat, lng):
        for u in units.values():
            b = u["bb"]
            if not (b[0] <= lng <= b[2] and b[1] <= lat <= b[3]):
                continue
            for p in u["polys"]:
                if in_ring(lng, lat, p[0]) and not any(in_ring(lng, lat, h) for h in p[1:]):
                    return u
        return None

    # 법정동·리(사이트의 동네 이름) → 행정동: 건축물대장 집계의 동네 대표점으로 찾는다
    places = {}
    AG = load("aging.json", {"places": []})
    for p in AG["places"]:
        u = find(p["at"][0], p["at"][1]) if p.get("at") else None
        if not u:
            m = re.match(r"^(\S+[읍면])", p["place"])
            u = units.get(m.group(1)) if m else units.get(p["place"])
        if u:
            places[p["place"]] = u["name"]

    def by_addr(addr):
        """주소 글자로 행정동 찾기: 읍·면 이름, 괄호 속 법정동 '(가경동', 지번 '사직동 119'"""
        a = re.sub(r"\s+", " ", addr or "").replace("오송옵", "오송읍")
        for nm, u in units.items():
            if nm.endswith(("읍", "면")) and nm in a:
                return u
        for pat in (r"\(([^,()\s]+?(?:동|가))[,)\s]", r"\s([^\s()]+?(?:동|가))\s+(?:산\s?)?\d"):
            for m in re.finditer(pat, a):
                nm = m.group(1)
                if nm in places:
                    return units.get(places[nm])
                if nm in units:
                    return units[nm]
        for m in re.finditer(r"\s([^\s()]+?리)\s+(?:산\s?)?\d", a):  # 읍·면 없이 '내수리 36-11'
            hit = [v for k, v in places.items() if k.endswith(" " + m.group(1))]
            if len(set(hit)) == 1:
                return units.get(hit[0])
        return None

    BS = load("busstops.json", {"stops": []})
    for s in BS["stops"]:
        u = find(s[1], s[2])
        if u:
            u["n"]["bus"] += 1
    PI = load("pois.json", {})
    for k in ("park", "lib", "market", "senior"):
        for s in PI.get(k, []):
            u = find(s[1], s[2])
            if u:
                u["n"][k] += 1
    FA = load("facilities.json", {})   # make_facilities.py: 학교·병원·약국·슈퍼·편의점 좌표
    for k in ("school", "hospital", "pharmacy", "mart"):
        for s in FA.get(k, []):
            u = find(s[1], s[2])
            if u:
                u["n"][k] += 1
    ND = load("newdata.json", {"items": {}, "meta": {}})   # newdata.py: 주소·좌표 [이름, 종류, 주소, 위도, 경도]
    active = set(k for k, _ in KINDS if k not in NEW_KINDS)
    miss = {}
    for k in NEW_KINDS:
        rows = ND.get("items", {}).get(k) or []
        if not rows:
            print(f"  {k}: 자료 없음 → 점수에서 뺌")
            continue
        active.add(k)
        lost = []
        for name, typ, addr, lat, lng in rows:
            u = find(lat, lng) if lat is not None else None
            u = u or by_addr(addr)
            if u:
                u["n"][k] += 1
            else:
                lost.append(name)
        miss[k] = len(lost)
        print(f"  {k}: {len(rows)}곳 중 동 못 찾음 {len(lost)}곳 {lost[:8]}")

    L = sorted(units.values(), key=lambda u: u["name"])
    for k, _ in KINDS:
        pa = pct_ranks([u["n"][k] / u["area"] for u in L])
        pp = pct_ranks([u["n"][k] / max(1, u["pop"]) * 1000 for u in L])
        for u, a, b in zip(L, pa, pp):
            u.setdefault("pa", {})[k] = round(a)
            u.setdefault("pp", {})[k] = round(b)
    # 4개 분야 × 면적(60%)·인구(40%) 가중. 분야끼리는 같은 비중(25%)
    for u in L:
        cats = {}
        for cat, members in CATEGORIES.items():
            mm = {k: w for k, w in members.items() if k in active}
            cats[cat] = round(sum(w * (W_AREA * u["pa"][k] + W_POP * u["pp"][k]) for k, w in mm.items()) / sum(mm.values()))
        u["cats"] = cats
        u["score"] = round(sum(cats.values()) / len(CATEGORIES))
        u["sa"] = round(sum(u["pa"].values()) / len(KINDS))   # 면적당만
        u["sp"] = round(sum(u["pp"].values()) / len(KINDS))   # 인구당만
    L.sort(key=lambda u: (-u["score"], u["name"]))
    for i, u in enumerate(L):
        u["rank"] = 1 + sum(1 for w in L if w["score"] > u["score"])

    BM, PM = BS.get("meta", {}), PI.get("meta", {})
    out = {
        "meta": {
            "name": "터놓고 생활권 점수",
            "pop_month": PO["meta"].get("month", ""),
            "bus_got": BM.get("got", ""), "poi_got": PM.get("got", "") if isinstance(PM, dict) else "",
            "kinds": KINDS,
            "categories": {c: [k for k in m if k in active] for c, m in CATEGORIES.items()},
            "kind_weights": {c: {k: w for k, w in m.items() if k in active} for c, m in CATEGORIES.items()},
            "weights": {"area": W_AREA, "pop": W_POP},
            "new_got": ND.get("meta", {}).get("got", ""), "unmatched": miss,
            "sources": ["국토교통부 (TAGO) 버스정류소정보", "전국 도시공원·도서관·전통시장·마을회관및경로당 표준데이터",
                        "한국교육시설안전원 전국초중등학교위치표준데이터", "소상공인시장진흥공단 상가(상권)정보 (병원·의원·약국·슈퍼·편의점)",
                        "전국주차장정보표준데이터", "소방청 119안전센터 현황", "경찰청 치안센터 주소(소방청·해양경찰청·경찰청 통합)",
                        "충청북도 공공체육시설 현황", "청주시시설관리공단 운영 시설 위치 정보", "문화체육관광부 전국 문화기반시설 총람(2025)",
                        "건강보험심사평가원 요양기관 개설 현황(요양병원)", "충청북도 어린이복지시설",
                        "행정안전부 행정동별 주민등록 인구", "행정동 경계 vuski/admdongkor (통계청 SGIS 기반)"],
        },
        "units": [{
            "name": u["name"], "gu": u["gu"], "codes": u["codes"], "pop": u["pop"], "area": round(u["area"], 2),
            "n": u["n"], "pa": u["pa"], "pp": u["pp"], "sa": u["sa"], "sp": u["sp"], "score": u["score"], "rank": u["rank"],
            "cats": u["cats"],
            "polys": u["polys"],
        } for u in L],
        "places": places,
    }
    save("life.json", out)
    print(f"life.json: 동네 {len(L)}곳 · 인구 {PO['meta'].get('month')} · 1위 {L[0]['name']} {L[0]['score']}점 · 법정동 연결 {len(places)}곳")
    for u in L:
        print(f"  {u['rank']:>2} {u['score']:>3} (면적 {u['sa']:>3} / 인구 {u['sp']:>3}) {u['gu']} {u['name']} {u['n']} 인구 {u['pop']:,}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "pop":
        pop(sys.argv[2:])
    elif cmd == "hist":
        build_hist()
    else:
        build()
