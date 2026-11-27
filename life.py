"""터놓고 생활권 점수 → life.json

동네(행정동)마다 생활시설 15가지를 4개 분야(교통·생활 / 의료·안전 / 여가·문화 / 돌봄·교육)로 묶어 얼마나 갖춰져 있는지를 두 가지로 잰다.
  · 면적당: 1㎢ 안에 몇 곳인지 (걸어서 닿기 쉬운지)
  · 인구당: 주민 1천 명당 몇 곳인지 (사람 수에 비해 넉넉한지)
시설마다 두 값을 청주 동네끼리 비교해 백분위(0~100)로 바꾸고, 면적 50%·인구 50%로 합친다.
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
         ("parking", "주차장"), ("safety", "119안전센터"), ("sports", "공공체육시설"), ("culture", "문화시설"),
         ("care", "요양병원"), ("child", "어린이복지시설"),
         # make_extra.py (상가정보)
         ("academy", "학원"), ("bigstore", "대형마트·백화점")]
NEW_KINDS = ("parking", "safety", "sports", "culture", "care", "child")
# 생활권 분야 (4개). 분야끼리 같은 비중, 분야 안 시설도 같은 비중. 자료를 못 받은 시설은 빼고 계산한다
CATEGORIES = {   # 분야 안 시설 가중치. 지금은 같은 비중 (근거 없는 차등을 두지 않음). 자료가 빠진 시설은 빼고 다시 나눔
    "교통·생활": {"bus": 1, "mart": 1, "bigstore": 1, "market": 1, "parking": 1},
    "의료·안전": {"hospital": 1, "pharmacy": 1, "safety": 1},
    "여가·문화": {"park": 1, "lib": 1, "sports": 1, "culture": 1},
    "돌봄·교육": {"school": 1, "academy": 1, "child": 1, "senior": 1, "care": 1},
}
W_AREA, W_POP = 0.5, 0.5   # 면적당(시설 밀도) 50%, 인구당(주민 대비 공급) 50%


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


def pct_low(vals):
    """작을수록 좋은 값(거리)의 백분위: 가장 가까운 동네 100, 가장 먼 동네 0"""
    n = len(vals)
    return [(sum(1 for w in vals if w > v) + (sum(1 for w in vals if w == v) - 1) / 2) / (n - 1) * 100 if n > 1 else 50 for v in vals]


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


CITY_DIRS = {"청주": "", "충주": "chungju"}
MERGE_CJ = {"교현2동": "교현·안림동"}   # 충주: 법정동 교현동이 두 행정동으로 나뉨


def build(cities=("청주",), out_name="life.json"):
    def loadc(name, default=None):
        """도시별 같은 이름 파일을 모두 읽어 리스트로 (청주=저장소 맨 위, 충주=chungju/)"""
        return [load(os.path.join(CITY_DIRS[c], name), default) for c in cities]
    units = {}
    if "청주" in cities:
        D = load("dongs.json")
        PO = load("population.json")
        if not D or not PO:
            sys.exit("dongs.json 또는 population.json이 없습니다.")
        for d in D["dongs"]:
            nm = MERGE.get(d["name"], d["name"])
            u = units.setdefault(nm, {"name": nm, "gu": d["gu"], "codes": [], "polys": [], "pop": 0, "area": 0.0})
            u["codes"].append(d["code"])
            u["polys"].extend(d["polys"])
            u["pop"] += PO["pop"].get(d["code"], 0)
    if "충주" in cities:
        CB = load("chungbuk-dongs.json")
        PC = load(os.path.join("chungju", "population.json"))
        for d in CB["dongs"]:
            if d.get("sgg") != "43130":
                continue
            nm0 = d["name"].replace("충주시 ", "").strip()
            nm = MERGE_CJ.get(nm0, nm0)
            u = units.setdefault(nm, {"name": nm, "gu": "충주시", "codes": [], "polys": [], "pop": 0, "area": 0.0})
            u["codes"].append(d["code"])
            u["polys"].extend(d["polys"])
            u["pop"] += PC["pop"].get(d["code"], 0)
    lat0 = 36.8 if "충주" in cities else 36.64
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
    AG = {"places": [], "complexes": []}
    for a_ in loadc("aging.json", {"places": [], "complexes": []}):
        AG["places"] += a_.get("places", []); AG["complexes"] += a_.get("complexes", [])
    OSM = [x for nd in loadc("newdata.json", {}) for x in (nd or {}).get("apts", [])]
    # 대표점이 없는 법정동·리(충주는 필지 자료가 없음): 오픈스트리트맵 마을·동 이름 점으로 채움
    vil = {}
    for nm_, typ_, la_, lo_ in OSM:
        if typ_ in ("village", "hamlet", "quarter", "neighbourhood", "suburb", "town"):
            vil.setdefault(nm_, []).append((la_, lo_))
    for p in AG["places"]:
        if p.get("at"):
            continue
        parts = p["place"].split(" ")
        cands = vil.get(parts[-1], [])
        if len(parts) == 2:   # '주덕읍 신양리': 같은 읍·면 행정동 안의 점만
            uu = units.get(parts[0])
            cands = [q for q in cands if uu and find(q[0], q[1]) is uu] or (vil.get(parts[0], []) if not cands else [])
        if cands:
            p["at"] = [cands[0][0], cands[0][1]]
    for p in AG["places"]:
        u = find(p["at"][0], p["at"][1]) if p.get("at") else None
        if not u:
            m = re.match(r"^(\S+[읍면])", p["place"])
            u = units.get(m.group(1)) if m else units.get(p["place"])
        if not u and p.get("gu") == "충주시":   # '성내동' → '성내·충인동'
            b_ = re.sub(r"동$", "", p["place"])
            alt = [w for w in units.values() if w["gu"] == "충주시" and b_ in [re.sub(r"동$", "", x) for x in w["name"].split("·")]]
            u = alt[0] if len(alt) == 1 else None
        if u and p.get("gu") and u["gu"] != p["gu"]:  # 대표점이 옆 구 행정동에 떨어진 경우 (예: 용담동 → 율량·사천동)
            base = re.sub(r"\d*(동|가)$", "", p["place"].split(" ")[0])
            alt = [w for w in units.values() if w["gu"] == p["gu"] and base and base in w["name"]]
            u = alt[0] if len(alt) == 1 else u
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

    # 주민이 사는 곳: 건축물대장 법정동·리 대표점마다 단독주택 수 + 아파트 세대 수를 무게로 (aging.json)
    hh = {}
    for c in AG.get("complexes", []):
        hh[c.get("place")] = hh.get(c.get("place"), 0) + (c.get("hh") or 0)
    # 아파트 단지 위치: 오픈스트리트맵의 이름 있는 아파트(newdata.json의 apts)와 건축물대장 단지 이름을 맞춤
    #  (같은 법정동 대표 지점에서 4㎞ 안, 맞은 건물들의 중앙값). 못 맞춘 단지는 법정동·리 대표 지점에 남김
    kx0, ky0 = 111.32 * math.cos(math.radians(36.64)), 110.57
    at0 = {p["place"]: p["at"] for p in AG["places"] if p.get("at")}

    def apt_norm(x):
        x = re.sub(r"\(.*?\)", "", x or "")
        x = re.sub(r"\d+\s*동$", "", x.strip())
        x = re.sub(r"(아파트|APT|apt|단지|\s|·|-|\.)", "", x)
        return re.sub(r"(청주|충주|충북|흥덕|상당|서원|청원)", "", x).lower()
    osm = {}
    for nm, typ, la, lo in OSM:
        k = apt_norm(nm)
        if len(k) >= 2:
            osm.setdefault(k, []).append((la, lo))
    apt_pts, aph = [], {}
    for c in AG.get("complexes", []):
        k, base = apt_norm(c.get("name")), at0.get(c.get("place"))
        cand = [q for key, qs in osm.items() if key == k or (len(k) >= 3 and (k in key or key in k)) for q in qs]
        if base:
            cand = [q for q in cand if math.hypot((q[1] - base[1]) * kx0, (q[0] - base[0]) * ky0) < 4]
        if cand and c.get("hh"):
            la, lo = sorted(q[0] for q in cand)[len(cand) // 2], sorted(q[1] for q in cand)[len(cand) // 2]
            apt_pts.append((c["place"], la, lo, c["hh"]))
            aph[c["place"]] = aph.get(c["place"], 0) + c["hh"]
    apt_hh = sum(c.get("hh") or 0 for c in AG.get("complexes", []))
    print(f"  아파트 단지 위치: {len(apt_pts)}/{len(AG.get('complexes', []))}곳, 세대 {sum(a[3] for a in apt_pts):,}/{apt_hh:,}")

    homes, ptof = [], {}
    for p in AG["places"]:
        if not p.get("at") or p["place"] not in places:
            continue
        w = sum((p.get("y", {}).get("house") or {}).values()) + hh.get(p["place"], 0) - aph.get(p["place"], 0)
        homes.append((units[places[p["place"]]], p["at"][0], p["at"][1], max(1, w)))
        ptof[p["place"]] = (p["at"][0], p["at"][1], max(1, w))
    for pl, la, lo, w in apt_pts:
        u_ = find(la, lo) or (units.get(places.get(pl)) if places.get(pl) else None)
        if u_:
            homes.append((u_, la, lo, w))

    def addr_point(addr):
        """좌표 없는 시설: 주소 속 법정동·리의 대표점 (읍·면만 알면 그 읍·면에서 집이 가장 많은 곳)"""
        a = re.sub(r"\s+", " ", addr or "").replace("오송옵", "오송읍")
        for pat in (r"\(([^,()\s]+?(?:동|가))[,)\s]", r"\s([^\s()]+?(?:동|가))\s+(?:산\s?)?\d"):
            for m in re.finditer(pat, a):
                if m.group(1) in ptof:
                    return ptof[m.group(1)][:2]
        for m in re.finditer(r"\s(\S+[읍면])\s?(\S+리)\b", a):
            k = f"{m.group(1)} {m.group(2)}"
            if k in ptof:
                return ptof[k][:2]
        for m in re.finditer(r"\s([^\s()]+?리)\s+(?:산\s?)?\d", a):
            hit = [v for k, v in ptof.items() if k.endswith(" " + m.group(1))]
            if len(hit) == 1:
                return hit[0][:2]
        for nm in units:
            if nm.endswith(("읍", "면")) and nm in a:
                c = [v for k, v in ptof.items() if k.startswith(nm + " ")]
                if c:
                    return max(c, key=lambda v: v[2])[:2]
        return None

    pts = {k: [] for k, _ in KINDS}   # 시설 위치 (거리 계산용)
    sub = {}                          # 한 항목 안의 다른 시설 (119안전센터 / 치안센터): 거리를 따로 잰 뒤 평균

    def add(k, lat, lng, addr="", typ=""):
        u = find(lat, lng) if lat is not None else None
        u = u or (by_addr(addr) if addr else None)
        pt = (lat, lng) if lat is not None else (addr_point(addr) if addr else None)
        if pt:
            pts[k].append(pt)
            if k == "safety":
                sub.setdefault(typ or "기타", []).append(pt)
        if u:
            u["n"][k] += 1
        return u

    BSs = loadc("busstops.json", {"stops": []}); BS = BSs[0] or {}
    for b_ in BSs:
        for s in (b_ or {}).get("stops", []):
            add("bus", s[1], s[2])
    PIs = loadc("pois.json", {}); PI = PIs[0] or {}
    for p_ in PIs:
        for k in ("park", "lib", "market", "senior"):
            for s in (p_ or {}).get(k, []):
                add(k, s[1], s[2])
    NDs = loadc("newdata.json", {"items": {}, "meta": {}})   # newdata.py: 주소·좌표 [이름, 종류, 주소, 위도, 경도]
    ND = NDs[0] or {}
    NDI = {}
    for n_ in NDs:
        for k, v in (n_ or {}).get("items", {}).items():
            NDI.setdefault(k, []).extend(v)
    for c_, FA in zip(cities, loadc("facilities.json", {})):   # make_facilities.py: 학교·병원·약국·슈퍼·편의점 좌표
        mine = load(os.path.join(CITY_DIRS[c_], "newdata.json"), {}).get("items", {})
        for k in ("school", "hospital", "pharmacy", "mart"):
            if mine.get(k):
                continue   # 병원·약국은 심평원 자료가 있으면 그것으로 셈 (상가정보는 의원이 많이 빠져 있음)
            for s in (FA or {}).get(k, []):
                add(k, s[1], s[2])
    EXs = loadc("extra.json", {}); EX = {}   # make_extra.py: 학원·대형마트·백화점 좌표
    for e_ in EXs:
        for k in ("academy", "bigstore"):
            for x in (e_ or {}).get(k, []):
                add(k, x[1], x[2])
                EX.setdefault(k, []).append(x)
    active = set(k for k, _ in KINDS if k not in NEW_KINDS and (k not in ("academy", "bigstore") or EX.get(k)))
    miss = {}
    for k in NEW_KINDS + ("hospital", "pharmacy"):
        rows = NDI.get(k) or []
        if not rows:
            if k in NEW_KINDS:
                print(f"  {k}: 자료 없음 → 점수에서 뺌")
            continue
        active.add(k)
        if k == "safety":   # 경찰 치안센터는 청주에 2곳(낭성·북이)뿐이라 거리 지표로 쓸 수 없어 119안전센터만 씀
            rows = [r for r in rows if r[1] == "119안전센터"]
        lost = [name for name, typ, addr, lat, lng in rows if not add(k, lat, lng, addr, typ)]
        miss[k] = len(lost)
        print(f"  {k}: {len(rows)}곳 중 동 못 찾음 {len(lost)}곳 {lost[:8]}")

    L = sorted(units.values(), key=lambda u: u["name"])
    # 드문 시설(절반 넘는 동에 하나도 없음): 동 안 개수 대신 '집에서 가장 가까운 시설까지 거리'(직선, 주민 수 가중 평균)
    kx, ky = 111.32 * math.cos(math.radians(36.64)), 110.57
    rare = [k for k, _ in KINDS if k in active and sum(1 for u in L if u["n"][k] == 0) > len(L) / 2]
    def near(hs, P):
        tot = sum(h[3] for h in hs)
        return sum(h[3] * min(math.hypot((h[2] - q[1]) * kx, (h[1] - q[0]) * ky) for q in P) for h in hs) / tot

    for k in rare:
        groups = list(sub.values()) if k == "safety" and len(sub) > 1 else [pts[k]]
        groups = [g for g in groups if g]
        if not groups:
            continue
        for u in L:
            hs = [h for h in homes if h[0] is u]
            if hs:
                u.setdefault("d", {})[k] = round(sum(near(hs, g) for g in groups) / len(groups), 2)
    for k, _ in KINDS:
        if k in rare:
            sc = pct_low([u.get("d", {}).get(k, 99) for u in L])
            pa = pp = sc
        else:
            pa = pct_ranks([u["n"][k] / u["area"] for u in L])
            pp = pct_ranks([u["n"][k] / max(1, u["pop"]) * 1000 for u in L])
        for u, x, y in zip(L, pa, pp):
            u.setdefault("pa", {})[k] = round(x)
            u.setdefault("pp", {})[k] = round(y)
    # 4개 분야 × 면적·인구 가중. 분야끼리는 같은 비중(25%)
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
            "rare": rare,
            "apt_located": [len(apt_pts), len(AG.get("complexes", [])), sum(a[3] for a in apt_pts), apt_hh],
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
            "cats": u["cats"], "d": u.get("d", {}),
            "polys": u["polys"],
        } for u in L],
        "places": places,
    }
    save(out_name, out)
    print(f"life.json: 동네 {len(L)}곳 · 인구 {PO['meta'].get('month')} · 1위 {L[0]['name']} {L[0]['score']}점 · 법정동 연결 {len(places)}곳")
    for u in L:
        print(f"  {u['rank']:>2} {u['score']:>3} (면적 {u['sa']:>3} / 인구 {u['sp']:>3}) {u['gu']} {u['name']} {u['n']} 인구 {u['pop']:,}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "pop":
        pop(sys.argv[2:])
    elif cmd == "hist":
        build_hist()
    elif cmd == "all":   # 청주+충주 함께 (확인용 파일)
        build(("청주", "충주"), "life-all.json")
    else:
        build()
