"""생활권 점수 추가 자료 (2026-11 승인분) → newdata.json

  python newdata.py probe    # 자료별 최신 판, 응답 항목 이름 확인 (로그만)
  python newdata.py update   # 청주 행만 받아 newdata.json 저장 (주소만 있는 곳은 좌표로 바꿈)
인증키: DATA_GO_KR_KEY (공공데이터포털에서 활용신청 승인된 계정), VWORLD_KEY(있으면 주소→좌표)

받는 자료 (생활권 점수 분야)
  parking   전국주차장정보표준데이터                         교통·생활
  safety    소방청 119안전센터 현황 + 경찰청 치안센터 주소     의료·안전
  sports    충청북도 공공체육시설 + 청주시시설관리공단 체육시설 여가·문화
  culture   문화기반시설 총람(culture.json, 손으로 넣음)       여가·문화
  hospital  건강보험심사평가원 요양기관 개설 현황: 병원·의원·보건소(치과·한방 제외) 의료·안전
  pharmacy  같은 자료: 약국                                    의료·안전
  care      같은 자료: 요양병원                                돌봄·교육
  child     충청북도 어린이복지시설                           돌봄·교육
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "newdata.json")
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "turnoko-newdata/1.0 (https://local-rader.github.io)"}

# 승인된 자료 (공공데이터포털 자료 번호). 파일데이터는 odcloud API로 받는다.
FILES = {
    "safety119": 15065056,     # 소방청_119안전센터 현황
    "police": 15112633,        # 소방청(119안전센터)_해양경찰청(파출장소)_경찰청(치안센터)_주소
    "cityfac": 15143611,       # 청주도시공사(청주시시설관리공단)_운영 시설 위치 정보
    "sports": 15083343,        # 충청북도_공공체육시설 현황
    "care": 15051057,          # 건강보험심사평가원_요양기관 개설 현황
    "child": 15090401,         # 충청북도_어린이복지시설
}
PARKING = "https://api.data.go.kr/openapi/tn_pubr_prkplce_info_api"  # 전국주차장정보표준데이터
BBOX = (36.38, 36.86, 127.22, 127.78)  # 청주시 범위 (lat, lat, lng, lng)


def key():
    k = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if not k:
        sys.exit("DATA_GO_KR_KEY가 없습니다.")
    return k


def fetch(url, tries=3, timeout=30):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                raw = r.read()
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("cp949", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 + 5 * i)
    raise RuntimeError(f"접속 실패: {str(last).replace(key(), 'KEY')}")


def versions(pid):
    """odcloud 자료의 판 목록 (최신 먼저): [(경로, 제목)]"""
    doc = json.loads(fetch(f"https://infuser.odcloud.kr/oas/docs?namespace={pid}/v1"))
    items = [(p, (s.get("get") or {}).get("summary", "")) for p, s in doc.get("paths", {}).items()]
    return sorted(items, key=lambda x: re.findall(r"\d{8}", x[1])[-1:] or [""], reverse=True)


def page_all(path, per=1000):
    out, page = [], 1
    while True:
        url = f"https://api.odcloud.kr/api{path}?" + urllib.parse.urlencode({"page": page, "perPage": per, "serviceKey": key()})
        j = json.loads(fetch(url))
        d = j.get("data") or []
        out.extend(d)
        if not d or page * per >= int(j.get("totalCount") or 0):
            return out
        page += 1
        time.sleep(0.2)


def latest_with(pid, need):
    """칸 이름에 need 중 하나가 들어 있는 가장 최신 판의 전체 행"""
    for path, title in versions(pid):
        head = json.loads(fetch(f"https://api.odcloud.kr/api{path}?" + urllib.parse.urlencode({"page": 1, "perPage": 1, "serviceKey": key()})))
        cols = list((head.get("data") or [{}])[0].keys())
        if any(any(n in c for n in need) for c in cols):
            rows = page_all(path)
            print(f"  {pid}: {title} ({len(rows)}행) 칸={cols}")
            return title, rows
    raise RuntimeError(f"{pid}: 주소 칸이 있는 판이 없습니다.")


def val(r, *names):
    for n in names:
        for k, v in r.items():
            if n in k and v not in (None, ""):
                return str(v).strip()
    return ""


def is_cj(addr):
    return bool(re.search(r"(충청북도|충북)\s*청주", addr or ""))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def item(name, sub, addr, lat=None, lng=None):
    lat, lng = num(lat), num(lng)
    if lat is None or lng is None or not (BBOX[0] < lat < BBOX[1] and BBOX[2] < lng < BBOX[3]):
        lat = lng = None
    return [name, sub, addr, lat, lng]


# ---------- 자료별 ----------
def get_safety():
    out, src = [], []
    t, rows = latest_with(FILES["safety119"], ["주소"])
    src.append(t)
    for r in rows:
        a = val(r, "주소")
        if is_cj(a):
            out.append(item(val(r, "119안전센터명", "센터명"), "119안전센터", a))
    try:
        t, rows = latest_with(FILES["police"], ["주소"])
        src.append(t)
        for r in rows:
            a, org = val(r, "주소"), val(r, "부처", "시도청", "기관")
            nm = val(r, "치안센터명", "센터명", "시설명")
            if not is_cj(a):
                continue
            if org != "경찰청" or "119" in nm:
                continue  # 119안전센터는 위 소방청 자료(더 최신)로 세고, 해양경찰 파출장소는 뺀다
            out.append(item(nm, "치안센터", a))
    except Exception as e:  # noqa: BLE001
        print("  치안센터 자료 실패:", e)
    return out, src


def get_sports():
    out, src = [], []
    t, rows = latest_with(FILES["sports"], ["주소"])
    src.append(t)
    for r in rows:
        a = val(r, "소재지지번주소") or val(r, "소재지도로명주소", "주소")
        if is_cj(a) or val(r, "소재지", "시군") == "청주시":
            out.append(item(val(r, "시설명"), "공공체육시설", a))
    t, rows = latest_with(FILES["cityfac"], ["위도", "주소"])
    src.append(t)
    culture = []
    for r in rows:
        a, kind = val(r, "도로명주소", "주소"), val(r, "구분")
        it = item(val(r, "건물명", "시설명"), kind, a, val(r, "위도"), val(r, "경도"))
        if "체육" in kind:
            out.append(it)
        elif "문화" in kind:
            culture.append(it)
    return out, culture, src


HOSP_TYPES = ("의원", "병원", "종합병원", "상급종합병원", "정신병원", "보건소", "보건지소", "보건진료소", "보건의료원")


def get_hira():
    """건강보험심사평가원 요양기관 개설 현황 → 병원·의원(치과·한방·조산원 제외), 약국, 요양병원"""
    t, rows = latest_with(FILES["care"], ["주소"])
    out = {"hospital": [], "pharmacy": [], "care": []}
    kinds = {}
    for r in rows:
        a, typ = val(r, "도로명주소"), val(r, "요양종별")
        if not is_cj(a):
            continue
        kinds[typ] = kinds.get(typ, 0) + 1
        it = item(val(r, "요양기관명"), typ, a)
        if typ in HOSP_TYPES:
            out["hospital"].append(it)
        elif typ == "약국":
            out["pharmacy"].append(it)
        elif typ == "요양병원":
            out["care"].append(it)
    print("  청주 요양기관 종별:", kinds)
    return out, [t]


def get_child():
    t, rows = latest_with(FILES["child"], ["주소"])
    return [item(val(r, "시설명"), val(r, "구분"), val(r, "주소")) for r in rows if is_cj(val(r, "주소"))], [t]


def get_parking():
    out, page = [], 1
    while True:
        q = {"serviceKey": key(), "pageNo": page, "numOfRows": 1000, "type": "json"}
        j = json.loads(fetch(PARKING + "?" + urllib.parse.urlencode(q)))
        body = (j.get("response") or j).get("body") or {}
        items = body.get("items") or []
        if isinstance(items, dict):
            items = items.get("item", [])
        for it in items:
            a = it.get("rdnmadr") or it.get("lnmadr") or ""
            if "청주" not in (a + (it.get("institutionNm") or "")):
                continue
            out.append(item(it.get("prkplceNm", ""), f"{it.get('prkplceSe', '')}·{it.get('prkplceType', '')}",
                            it.get("lnmadr") or a, it.get("latitude"), it.get("longitude")))
        if not items or page * 1000 >= int(body.get("totalCount") or 0):
            break
        page += 1
        time.sleep(0.2)
    return out, ["전국주차장정보표준데이터"]


BIG = re.compile(r"이마트(?!\s?24|\s?에브리데이)|홈플러스(?!\s?익스프레스)|롯데마트|코스트코|트레이더스|백화점|아울렛|NC\s?(청주|백화점)|하나로클럽|롯데몰|스타필드")


def get_bigstore():
    """대형마트·백화점·아울렛: 오픈스트리트맵(Overpass)에서 청주 범위의 대형 매장 이름으로 찾음"""
    q = f"""[out:json][timeout:60];(
      nwr["shop"~"department_store|mall|supermarket|wholesale"]({BBOX[0]},{BBOX[2]},{BBOX[1]},{BBOX[3]});
    );out center tags;"""
    j = json.loads(fetch("https://overpass-api.de/api/interpreter?" + urllib.parse.urlencode({"data": q}), timeout=90))
    out, seen = [], set()
    for e in j.get("elements", []):
        t = e.get("tags", {})
        nm = t.get("name", "") or t.get("brand", "")
        if not BIG.search(nm + " " + t.get("brand", "")):
            continue
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lng = e.get("lon") or (e.get("center") or {}).get("lon")
        it = item(nm, t.get("shop", ""), t.get("addr:full", "") or t.get("addr:street", ""), lat, lng)
        if it[3] is None:
            continue
        key = (re.sub(r"\s", "", nm), round(it[3], 3), round(it[4], 3))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    for x in out:
        print("   ", x[0], x[1], x[3], x[4])
    return out, ["OpenStreetMap (대형마트·백화점·아울렛 이름 기준)"]


def bigstore():
    """이미 받은 newdata.json에 대형 매장만 새로 받아 넣음"""
    out = json.load(open(OUT, encoding="utf-8"))
    rows, src = get_bigstore()
    out["items"]["bigstore"] = rows
    out["meta"]["source"]["bigstore"] = src
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("bigstore", len(rows))


# ---------- 주소 → 좌표 ----------
def has_dong_hint(addr):
    """주소만으로 동을 알 수 있으면 True: '(가경동' 괄호, 지번 '사직동 119', '오창읍 ' 같은 읍면"""
    return bool(re.search(r"\([^)]*?\S+(동|가)\b", addr) or re.search(r"\s\S+(동|가)\s+(산\s?)?\d", addr)
                or re.search(r"\S+(읍|면)\s", addr))


def clean(addr):
    a = re.sub(r"^충북\s*", "충청북도 ", addr.strip())
    a = re.sub(r"(충청북도|청주시|상당구|서원구|흥덕구|청원구)(?=\S)", r"\1 ", a)  # '충북청주시상당구단재로293'
    a = re.sub(r"\(.*?\)", "", a).split(",")[0]
    a = re.sub(r"(로|길)\s+(\d+번길)", r"\1\2", a)  # '충렬로 18번길' → '충렬로18번길' (도로명 표기)
    m = re.match(r"^(.*?\S(?:로|길))\s*(\d+(?:-\d+)?)(?!\d|-|번길)", a)
    return re.sub(r"\s+", " ", (m.group(1) + " " + m.group(2)) if m else a).strip()


def road_only(addr):
    m = re.match(r"^(.*?\S(?:로|길)) \d+(?:-\d+)?$", clean(addr))
    return m.group(1) if m else None


def geo_vworld(addr, k):
    for typ in ("road", "parcel"):
        q = {"service": "address", "request": "getcoord", "version": "2.0", "crs": "epsg:4326", "refine": "true",
             "simple": "false", "format": "json", "type": typ, "address": addr, "key": k,
             "domain": os.environ.get("VWORLD_DOMAIN", "") or "https://local-rader.github.io"}
        j = json.loads(fetch("https://api.vworld.kr/req/address?" + urllib.parse.urlencode(q), tries=1, timeout=15))
        res = j.get("response", {})
        if res.get("status") == "OK":
            p = res["result"]["point"]
            return float(p["y"]), float(p["x"])
    return None


def geo_osm(addr):
    q = {"q": addr, "format": "json", "countrycodes": "kr", "limit": 1}
    j = json.loads(fetch("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(q), tries=2, timeout=20))
    time.sleep(1.1)  # 이용 규칙: 1초에 1번
    return (float(j[0]["lat"]), float(j[0]["lon"])) if j else None


def geocode(items, road=True):
    vk = os.environ.get("VWORLD_KEY", "").strip()
    stat = {"vworld": 0, "osm": 0, "fail": 0, "skip": 0}
    vworld_ok = bool(vk)
    for it in items:
        if it[3] is not None or has_dong_hint(it[2]):
            stat["skip"] += 1
            continue
        a, got = clean(it[2]), None
        if vworld_ok:
            try:
                got = geo_vworld(a, vk)
                if got:
                    stat["vworld"] += 1
            except Exception as e:  # noqa: BLE001
                print("  브이월드 안 됨, 오픈스트리트맵으로:", str(e)[:120])
                vworld_ok = False
        if not got:
            try:
                got = geo_osm(a)
                if got:
                    stat["osm"] += 1
                elif road:  # 건물 번호가 지도에 없으면 도로(→ 큰길) 가운데 좌표로 근사
                    r1 = road_only(it[2])
                    r2 = re.sub(r"\d+번길$", "", r1) if r1 else None
                    for r in [x for x in (r1, r2) if x and re.search(r"\S(로|길)$", x)]:
                        got = geo_osm(r)
                        if got:
                            stat["road"] = stat.get("road", 0) + 1
                            print("  도로 기준 근사:", it[0], "|", r)
                            break
            except Exception as e:  # noqa: BLE001
                print("  오픈스트리트맵 실패:", a, str(e)[:80])
        if got and BBOX[0] < got[0] < BBOX[1] and BBOX[2] < got[1] < BBOX[3]:
            it[3], it[4] = round(got[0], 6), round(got[1], 6)
        else:
            stat["fail"] += 1
            print("  좌표 못 찾음:", it[0], "|", it[2])
    print("  주소→좌표:", stat)


def update():
    prev = {}
    try:
        prev = json.load(open(OUT, encoding="utf-8"))
    except (OSError, ValueError):
        pass
    out = {"meta": {"got": datetime.now(KST).strftime("%Y-%m-%d"), "fields": ["name", "type", "addr", "lat", "lng"], "source": {}},
           "items": {}}
    jobs = {"parking": get_parking, "safety": get_safety, "sports": get_sports, "hira": get_hira, "child": get_child,
            "bigstore": get_bigstore}
    for kind, fn in jobs.items():
        print(f"\n## {kind}")
        try:
            res = fn()
            if kind == "sports":
                rows, cul, src = res
                out["items"]["culture_city"] = cul
            elif kind == "hira":
                parts, src = res
                for k2, rows2 in parts.items():
                    out["items"][k2] = rows2
                    out["meta"]["source"][k2] = src
                    print(f"  {k2}: 청주 {len(rows2)}곳")
                continue
            else:
                rows, src = res
            out["items"][kind] = rows
            out["meta"]["source"][kind] = src
            print(f"  청주 {len(rows)}곳")
        except Exception as e:  # noqa: BLE001
            print(f"  실패, 이전 자료 유지: {e}")
            if prev.get("items", {}).get(kind):
                out["items"][kind] = prev["items"][kind]
    try:
        C = json.load(open(os.path.join(HERE, "culture.json"), encoding="utf-8"))
        out["items"]["culture"] = [item(n, t, a) for n, t, a in C["culture"]] + out["items"].pop("culture_city", [])
        out["meta"]["source"]["culture"] = [C["meta"]["source"], "청주시시설관리공단 운영 시설(문화시설)"]
    except (OSError, ValueError, KeyError) as e:
        print("culture.json 없음:", e)
    print("\n## 주소 → 좌표")
    for kind, rows in out["items"].items():
        print(f" [{kind}]")
        geocode(rows, road=kind not in ("hospital", "pharmacy"))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("\n저장:", {k: len(v) for k, v in out["items"].items()})


def geofix():
    """이미 받은 newdata.json에서 좌표 없는 곳만 다시 찾는다"""
    out = json.load(open(OUT, encoding="utf-8"))
    for kind, rows in out["items"].items():
        print(f" [{kind}]")
        geocode(rows, road=kind not in ("hospital", "pharmacy"))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))


def probe():
    for name, pid in FILES.items():
        print(f"\n## {name} ({pid})")
        try:
            for path, title in versions(pid)[:2]:
                head = json.loads(fetch(f"https://api.odcloud.kr/api{path}?" + urllib.parse.urlencode({"page": 1, "perPage": 1, "serviceKey": key()})))
                d = head.get("data") or []
                print(f"  {title} | total={head.get('totalCount')} | 예={json.dumps(d[0], ensure_ascii=False)[:300] if d else head}")
        except Exception as e:  # noqa: BLE001
            print("  실패:", e)


if __name__ == "__main__":
    {"probe": probe, "update": update, "geofix": geofix, "bigstore": bigstore}[sys.argv[1] if len(sys.argv) > 1 else "probe"]()
