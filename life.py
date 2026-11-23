"""터놓고 생활권 점수 → life.json

동네(행정동)마다 생활시설 5가지가 얼마나 갖춰져 있는지를 두 가지로 잰다.
  · 면적당: 1㎢ 안에 몇 곳인지 (걸어서 닿기 쉬운지)
  · 인구당: 주민 1천 명당 몇 곳인지 (사람 수에 비해 넉넉한지)
시설마다 두 값을 청주 동네끼리 비교해 백분위(0~100)로 바꾸고, 10개(5가지 × 2) 백분위의 평균을 점수로 쓴다.
공식 지표가 아니라 터놓고가 공공데이터로 정한 기준이다.

사용법
  python life.py pop 파일.xlsx   # 행정안전부 '행정동별 주민등록 인구 및 세대현황' 엑셀 → population.json
  python life.py build           # dongs.json·population.json·busstops.json·pois.json → life.json
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
KINDS = [("bus", "버스정류소"), ("park", "공원"), ("lib", "도서관"), ("market", "전통시장"), ("senior", "경로당")]


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


def pop(path):
    import openpyxl
    wb = openpyxl.load_workbook(open(path, "rb"))  # 확장자가 .xls여도 실제 xlsx면 읽힘
    ws = wb.active
    month, rows = "", {}
    for r in ws.iter_rows(values_only=True):
        cells = [c for c in r if c is not None]
        if not cells:
            continue
        for c in cells:
            m = re.match(r"(\d{4})년\s*(\d{1,2})월", str(c))
            if m and not month:
                month = f"{m.group(1)}-{int(m.group(2)):02d}"
        if len(r) < 3:
            continue
        code = str(r[0] or "").strip()
        if re.fullmatch(r"\d{10}", code) and code[5:] != "00000":
            try:
                n = int(str(r[2]).replace(",", ""))
            except (TypeError, ValueError):
                continue
            rows[code] = n
    if len(rows) < 30:
        sys.exit(f"행정동 인구가 {len(rows)}곳뿐입니다. 파일을 확인해 주세요.")
    save("population.json", {"meta": {"source": "행정안전부 행정동별 주민등록 인구 및 세대현황", "month": month,
                                      "note": "매월 말일 기준 총 거주자 수"}, "pop": rows})
    print(f"population.json: {month}, 행정동 {len(rows)}곳, 합계 {sum(rows.values()):,}명")


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

    L = sorted(units.values(), key=lambda u: u["name"])
    for k, _ in KINDS:
        pa = pct_ranks([u["n"][k] / u["area"] for u in L])
        pp = pct_ranks([u["n"][k] / max(1, u["pop"]) * 1000 for u in L])
        for u, a, b in zip(L, pa, pp):
            u.setdefault("pa", {})[k] = round(a)
            u.setdefault("pp", {})[k] = round(b)
    for u in L:
        u["score"] = round(sum(u["pa"].values()) / len(KINDS) / 2 + sum(u["pp"].values()) / len(KINDS) / 2)
        u["sa"] = round(sum(u["pa"].values()) / len(KINDS))   # 면적당만
        u["sp"] = round(sum(u["pp"].values()) / len(KINDS))   # 인구당만
    L.sort(key=lambda u: (-u["score"], u["name"]))
    for i, u in enumerate(L):
        u["rank"] = 1 + sum(1 for w in L if w["score"] > u["score"])

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

    BM, PM = BS.get("meta", {}), PI.get("meta", {})
    out = {
        "meta": {
            "name": "터놓고 생활권 점수",
            "pop_month": PO["meta"].get("month", ""),
            "bus_got": BM.get("got", ""), "poi_got": PM.get("got", "") if isinstance(PM, dict) else "",
            "kinds": KINDS,
            "sources": ["국토교통부 (TAGO) 버스정류소정보", "전국 도시공원·도서관·전통시장·마을회관및경로당 표준데이터",
                        "행정안전부 행정동별 주민등록 인구", "행정동 경계 vuski/admdongkor (통계청 SGIS 기반)"],
        },
        "units": [{
            "name": u["name"], "gu": u["gu"], "codes": u["codes"], "pop": u["pop"], "area": round(u["area"], 2),
            "n": u["n"], "pa": u["pa"], "pp": u["pp"], "sa": u["sa"], "sp": u["sp"], "score": u["score"], "rank": u["rank"],
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
        pop(sys.argv[2])
    else:
        build()
