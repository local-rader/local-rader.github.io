"""살기 좋은 동네 지수용 자료 → livable.json (청주) / chungju/livable.json (충주)

  python livable.py probe     # API 주소·항목 확인 (로그만)
  python livable.py update    # 받을 수 있는 것 모두 받기 (실패한 것은 이전 자료 유지)
도시: TURNOKO_CITY=충주 (기본 청주)
인증키: RTMS_KEY(전월세, 실거래가와 같은 계정), DATA_GO_KR_KEY(공원·버스노선·연령별 인구)

받는 것
  rent    국토교통부 아파트 전월세 실거래가 (최근 12개월) → 전세가율
  parks   전국도시공원정보표준데이터의 공원 면적
  routes  국토교통부 (TAGO) 버스노선정보: 노선별 경유 정류소 → 정류소마다 지나는 노선 수
  ages    행정안전부 지역별(법정동) 성별 연령별 주민등록 인구수 → 아동·20~40대 비율
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
CITYK = os.environ.get("TURNOKO_CITY", "청주").strip() or "청주"
CITY_DIR = {"청주": "", "충주": "chungju"}[CITYK]
OUT = os.path.join(HERE, CITY_DIR, "livable.json")
SGG = {"청주": ["43111", "43112", "43113", "43114"], "충주": ["43130"]}[CITYK]
BBOX = {"청주": (36.38, 36.86, 127.22, 127.78), "충주": (36.78, 37.23, 127.63, 128.15)}[CITYK]
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "turnoko-livable/1.0 (https://local-rader.github.io)"}
RENT = "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"
PARK = "https://api.data.go.kr/openapi/tn_pubr_public_cty_park_info_api"
ROUTE = "https://apis.data.go.kr/1613000/BusRouteInfoInqireService/"
AGES_PID = 15099158


def env(k):
    v = os.environ.get(k, "").strip()
    if not v:
        raise RuntimeError(f"{k}가 없습니다.")
    return v


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
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} {e.read()[:200]!r}"
        except Exception as e:  # noqa: BLE001
            last = str(e)[:200]
        time.sleep(2 + 4 * i)
    raise RuntimeError(f"접속 실패: {last}")


def hide(s):
    for k in ("RTMS_KEY", "DATA_GO_KR_KEY"):
        v = os.environ.get(k, "")
        if v:
            s = s.replace(v, "KEY").replace(urllib.parse.quote(v, safe=""), "KEY")
    return s


def items_of(text):
    """JSON 또는 XML 응답 → (total, items)"""
    t = text.lstrip()
    if t.startswith("{"):
        j = json.loads(t)
        body = (j.get("response") or j).get("body") or {}
        its = body.get("items") or []
        if isinstance(its, dict):
            its = its.get("item", [])
        if isinstance(its, dict):
            its = [its]
        return int(body.get("totalCount") or 0), its
    import xml.etree.ElementTree as ET
    root = ET.fromstring(t)
    code = (root.findtext(".//resultCode") or "").strip()
    if code and code not in ("00", "000", "0"):
        raise RuntimeError(f"resultCode {code} {root.findtext('.//resultMsg')}")
    return int(root.findtext(".//totalCount") or 0), [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]


def months(n):
    d = datetime.now(KST).replace(day=1) - timedelta(days=40)   # 신고 기한(30일) 지난 달부터
    out = []
    for _ in range(n):
        out.append(d.strftime("%Y%m"))
        d = d.replace(day=1) - timedelta(days=1)
    return out


# ---------- 전월세 ----------
def rent_key():
    """전월세 활용신청이 어느 계정 키로 됐는지 모르므로 둘 다 시험"""
    for name in ("RTMS_KEY", "DATA_GO_KR_KEY"):
        k = os.environ.get(name, "").strip()
        if not k:
            continue
        q = {"serviceKey": k, "LAWD_CD": SGG[0], "DEAL_YMD": months(1)[0], "pageNo": 1, "numOfRows": 1}
        try:
            items_of(fetch(RENT + "?" + urllib.parse.urlencode(q), tries=1))
            print(f"  전월세: {name} 사용")
            return k
        except Exception as e:  # noqa: BLE001
            print(f"  전월세 {name} 실패:", hide(str(e))[:120])
    raise RuntimeError("전월세 API를 쓸 수 있는 키가 없습니다.")


def get_rent():
    k = rent_key()
    rows = []
    for sg in SGG:
        for ym in months(12):
            page = 1
            while True:
                q = {"serviceKey": k, "LAWD_CD": sg, "DEAL_YMD": ym, "pageNo": page, "numOfRows": 1000}
                total, its = items_of(fetch(RENT + "?" + urllib.parse.urlencode(q)))
                for it in its:
                    dep = float(str(it.get("deposit") or "0").replace(",", "") or 0)
                    mon = float(str(it.get("monthlyRent") or "0").replace(",", "") or 0)
                    ar = float(it.get("excluUseAr") or 0)
                    rows.append([sg, (it.get("umdNm") or "").strip(), (it.get("aptNm") or "").strip(), ym, dep, mon, ar])
                if not its or page * 1000 >= total:
                    break
                page += 1
                time.sleep(0.2)
    jeonse = sum(1 for r in rows if r[5] == 0)
    print(f"  전월세 {len(rows):,}건 (전세 {jeonse:,})")
    return {"fields": ["sgg", "dong", "apt", "ym", "deposit_만원", "monthly_만원", "area_m2"], "rows": rows}


# ---------- 공원 면적 ----------
def get_parks():
    k = env("DATA_GO_KR_KEY")
    out, page = [], 1
    while True:
        q = {"serviceKey": k, "pageNo": page, "numOfRows": 1000, "type": "json"}
        total, its = items_of(fetch(PARK + "?" + urllib.parse.urlencode(q)))
        for it in its:
            addr = (it.get("rdnmadr") or "") + " " + (it.get("lnmadr") or "")
            if CITYK + "시" not in addr:
                continue
            try:
                lat, lng = float(it.get("latitude")), float(it.get("longitude"))
            except (TypeError, ValueError):
                continue
            if not (BBOX[0] < lat < BBOX[1] and BBOX[2] < lng < BBOX[3]):
                continue
            try:
                area = float(str(it.get("parkAr") or "0").replace(",", ""))
            except ValueError:
                area = 0.0
            out.append([it.get("parkNm", ""), it.get("parkSe", ""), round(lat, 6), round(lng, 6), area])
        if not its or page * 1000 >= total:
            break
        page += 1
        time.sleep(0.2)
    print(f"  공원 {len(out)}곳, 면적 합 {sum(p[4] for p in out) / 1e6:.2f}㎢")
    return {"fields": ["name", "kind", "lat", "lng", "area_m2"], "rows": out}


# ---------- 버스 노선 ----------
def tago(op, **q):
    q.update({"serviceKey": env("DATA_GO_KR_KEY"), "_type": "json", "numOfRows": q.get("numOfRows", 1000)})
    return items_of(fetch(ROUTE + op + "?" + urllib.parse.urlencode(q)))


def city_code():
    _, its = tago("getCtyCodeList")
    for it in its:
        if CITYK in str(it.get("cityname", "")):
            return str(it["citycode"])
    raise RuntimeError("도시코드 없음")


def get_routes():
    cc = city_code()
    routes, page = [], 1
    while True:
        total, its = tago("getRouteNoList", cityCode=cc, pageNo=page)
        routes += its
        if not its or page * 1000 >= total:
            break
        page += 1
    stops = {}
    for r in routes:
        rid = r.get("routeid")
        _, ss = tago("getRouteAcctoThrghSttnList", cityCode=cc, routeId=rid, numOfRows=500)
        for s in ss:
            key = s.get("nodeid")
            try:
                lat, lng = float(s.get("gpslati")), float(s.get("gpslong"))
            except (TypeError, ValueError):
                continue
            st = stops.setdefault(key, [s.get("nodenm", ""), round(lat, 6), round(lng, 6), set()])
            st[3].add(str(r.get("routeno", rid)))
        time.sleep(0.1)
    names = sorted(set(x for v in stops.values() for x in v[3]))
    ix = {x: i for i, x in enumerate(names)}
    rows = [[v[0], v[1], v[2], sorted(ix[x] for x in v[3])] for v in stops.values()]
    print(f"  노선 {len(routes)}개, 정류소 {len(rows)}곳, 정류소당 노선 최대 {max((len(r[3]) for r in rows), default=0)}")
    return {"fields": ["name", "lat", "lng", "route_idx"], "routes": names, "rows": rows, "route_count": len(routes)}


# ---------- 연령별 인구 (odcloud 파일데이터) ----------
def odc(path, page, per):
    q = {"page": page, "perPage": per, "serviceKey": env("DATA_GO_KR_KEY")}
    return json.loads(fetch(f"https://api.odcloud.kr/api{path}?" + urllib.parse.urlencode(q), timeout=60))


def age_versions():
    doc = json.loads(fetch(f"https://infuser.odcloud.kr/oas/docs?namespace={AGES_PID}/v1"))
    items = [(p, (s.get("get") or {}).get("summary", "")) for p, s in doc.get("paths", {}).items()]
    return sorted(items, key=lambda x: re.findall(r"\d{6,8}", x[1])[-1:] or [""], reverse=True)


def get_ages(ym=None):
    """ym(예: '202110')을 주면 그 달 판, 없으면 가장 최근 판"""
    vs = age_versions()
    print("  받을 수 있는 판:", len(vs), "개, 가장 오래된 것", [re.findall(r"\d{8}", t)[-1:] for _, t in vs][-3:])
    if ym == "oldest10":   # 5년 전 판이 없으면 남아 있는 가장 오래된 10월 판 (같은 달끼리 비교)
        hit = [(p, t) for p, t in vs if re.search(r"\d{4}10\d\d", t)]
        if not hit:
            raise RuntimeError("10월 판 없음")
        vs = hit[-1:]
    elif ym:
        hit = [(p, t) for p, t in vs if re.search(ym + r"\d\d", t)]
        if not hit:
            raise RuntimeError(f"{ym} 판 없음")
        vs = hit
    path, title = vs[0]
    head = odc(path, 1, 1)
    total = int(head.get("totalCount") or 0)
    print(f"  {title}: 전체 {total:,}행, 칸 예 {list((head.get('data') or [{}])[0].keys())[:12]}")
    out, page, per = [], 1, 1000
    while (page - 1) * per < total:
        d = odc(path, page, per).get("data") or []
        for r in d:   # 판마다 칸 이름이 조금씩 달라서(예: '0세남자'/'만0세남자') 법정동코드 앞 5자리로 거름
            if str(r.get("법정동코드", ""))[:5] in SGG:
                out.append(r)
        page += 1
        time.sleep(0.15)
    print(f"  {CITYK} 행 {len(out)}")
    return {"title": title, "rows": out}


# ---------- 고속도로 IC·KTX역 (오픈스트리트맵) ----------
def get_transit():
    """시 경계 밖 가까운 IC도 잡도록 범위를 넓혀서 받음. 고속도로(motorway) 위의 나들목만 (국도 교차로 제외), 분기점(JCT)은 드나들 수 없어 뺌"""
    b = (BBOX[0] - 0.15, BBOX[1] + 0.15, BBOX[2] - 0.18, BBOX[3] + 0.18)
    q = f"""[out:json][timeout:90];(
      way["highway"="motorway"]({b[0]},{b[2]},{b[1]},{b[3]})->.m;
      node(w.m)["highway"="motorway_junction"];
      nwr["railway"="station"]["name"~"^(오송|충주)(역)?$"]({b[0]},{b[2]},{b[1]},{b[3]});
    );out center tags;"""
    j, last = None, None
    for ep in ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
               "https://overpass.private.coffee/api/interpreter"):
        try:
            j = json.loads(fetch(ep + "?" + urllib.parse.urlencode({"data": q}), timeout=120))
            break
        except Exception as e:  # noqa: BLE001
            last = e
            print("  오버패스 실패, 다른 서버로:", ep, str(e)[:80])
    if j is None:
        raise RuntimeError(f"오버패스 서버 모두 실패: {last}")
    ic, st = [], []
    for e in j.get("elements", []):
        t = e.get("tags", {})
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lng = e.get("lon") or (e.get("center") or {}).get("lon")
        nm = t.get("name", "")
        if not (lat and lng):
            continue
        if t.get("railway") == "station":
            st.append([nm, round(lat, 6), round(lng, 6)])
        elif nm and not re.search(r"분기점|JC|JCT|휴게소", nm, re.I):
            ic.append([nm, round(lat, 6), round(lng, 6)])
    print(f"  IC {len(ic)}개 {sorted(set(x[0] for x in ic))[:30]}")
    print(f"  역 {st}")
    return {"fields": ["name", "lat", "lng"], "ic": ic, "station": st, "rows": ic + st}


JOBS = {"rent": get_rent, "parks": get_parks, "routes": get_routes, "ages": get_ages, "transit": get_transit,
        # 5년 전 같은 달: 학령기 아이가 들어오는 동네인지(같은 나이 또래가 5년 사이 늘었나) 보려고
        "ages_5y": lambda: get_ages("oldest10")}


def update(only=None):
    try:
        prev = json.load(open(OUT, encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    out = {"meta": {"got": datetime.now(KST).strftime("%Y-%m-%d"), "city": CITYK}}
    for k, fn in JOBS.items():
        if only and k not in only:
            if prev.get(k):
                out[k] = prev[k]
            continue
        print(f"\n## {k}")
        try:
            out[k] = fn()
        except Exception as e:  # noqa: BLE001
            print("  실패, 이전 자료 유지:", hide(str(e))[:300])
            if prev.get(k):
                out[k] = prev[k]
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("\n저장:", {k: (len(v.get("rows", [])) if isinstance(v, dict) else "") for k, v in out.items() if k != "meta"})


def probe():
    print("## rent")
    try:
        k = rent_key()
        q = {"serviceKey": k, "LAWD_CD": SGG[0], "DEAL_YMD": months(1)[0], "pageNo": 1, "numOfRows": 2}
        print("  ", hide(fetch(RENT + "?" + urllib.parse.urlencode(q), tries=1)[:600]).replace("\n", " "))
    except Exception as e:  # noqa: BLE001
        print("  실패:", hide(str(e))[:300])
    print("## parks")
    try:
        q = {"serviceKey": env("DATA_GO_KR_KEY"), "pageNo": 1, "numOfRows": 1, "type": "json"}
        print("  ", hide(fetch(PARK + "?" + urllib.parse.urlencode(q), tries=1)[:700]).replace("\n", " "))
    except Exception as e:  # noqa: BLE001
        print("  실패:", hide(str(e))[:300])
    print("## routes")
    try:
        cc = city_code()
        total, its = tago("getRouteNoList", cityCode=cc, numOfRows=2)
        print("  도시코드", cc, "노선", total, its[:2])
    except Exception as e:  # noqa: BLE001
        print("  실패:", hide(str(e))[:300])
    print("## ages")
    try:
        for p, t in age_versions()[:2]:
            h = odc(p, 1, 1)
            print("  ", t, h.get("totalCount"), json.dumps((h.get("data") or [{}])[0], ensure_ascii=False)[:500])
    except Exception as e:  # noqa: BLE001
        print("  실패:", hide(str(e))[:300])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"
    if cmd == "probe":
        probe()
    else:
        update(set(sys.argv[2].split(",")) if len(sys.argv) > 2 else None)
