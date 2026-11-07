"""
동네레이더 데이터 수집기

건축HUB 건축인허가정보(국토교통부) API에서 청주시 청원구의 인허가 기록을 받아
permits.json(원장)에 합치고, 웹사이트가 읽는 data.js를 만든다.

사용법
  python fetch.py discover            청원구 법정동·리 코드를 자동으로 찾아 codes.json에 저장
  python fetch.py update              최근 14일 사이 바뀐 기록을 받아 합치기 (매일 자동 실행용)
  python fetch.py update --since 20230101   그 날짜 이후 바뀐 기록을 모두 받기 (처음 한 번)
  python fetch.py build               permits.json으로 data.js만 다시 만들기
  python fetch.py parcels             필지번호로 필지 중심 좌표와 개별공시지가를 받아 parcels.json에 저장

인증키는 환경변수로 받는다. 코드에 직접 적지 않는다.
  DATA_GO_KR_KEY  공공데이터포털 일반 인증키 (건축인허가)
  VWORLD_KEY      브이월드 인증키 (국토교통부 2D데이터 API, 연속지적도). 없으면 parcels는 건너뛴다.
표준 라이브러리만 사용하므로 설치할 패키지가 없다.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://apis.data.go.kr/1613000/ArchPmsHubService/getApBasisOulnInfo"
HERE = os.path.dirname(os.path.abspath(__file__))
CODES = os.path.join(HERE, "codes.json")
STORE = os.path.join(HERE, "permits.json")
SITE = os.path.join(HERE, "data.js")
PARCELS = os.path.join(HERE, "parcels.json")
VWORLD = "https://api.vworld.kr/req/data"
KST = timezone(timedelta(hours=9))
PAUSE = 0.25  # 호출 사이 간격(초). 서버에 부담을 주지 않기 위함
MAX_EVENTS = 800


# ---------------------------------------------------------------- 공통 도구

def now_kst() -> datetime:
    return datetime.now(KST)


def load(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def api_key() -> str:
    key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if not key:
        sys.exit("인증키가 없습니다. 환경변수 DATA_GO_KR_KEY 를 설정하세요 (GitHub에서는 Secrets).")
    return key


def call(key, sigungu, bjdong, page=1, rows=100, since=None, until=None, tries=5):
    """API를 한 번 호출해 (전체 건수, 기록 목록)을 돌려준다."""
    params = {
        "serviceKey": key, "sigunguCd": sigungu, "bjdongCd": bjdong,
        "numOfRows": rows, "pageNo": page, "_type": "json",
    }
    if since:
        params["startDate"] = since
        params["endDate"] = until or now_kst().strftime("%Y%m%d")
    url = BASE + "?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dongne-radar-collector"})
            with urllib.request.urlopen(req, timeout=40) as r:
                text = r.read().decode("utf-8", errors="replace")
            if text.lstrip().startswith("<"):
                # 인증 오류 등은 JSON을 요청해도 XML로 온다
                msg = re.search(r"<returnAuthMsg>(.*?)</returnAuthMsg>|<resultMsg>(.*?)</resultMsg>", text)
                raise RuntimeError("API가 XML 오류를 돌려줬습니다: " + (msg.group(0) if msg else text[:300]))
            data = json.loads(text)
            head = data.get("response", {}).get("header", {})
            if str(head.get("resultCode", "00")) not in ("00", "0"):
                raise RuntimeError(f"API 오류: {head}")
            body = data["response"].get("body", {}) or {}
            total = int(body.get("totalCount") or 0)
            items = body.get("items") or []
            if isinstance(items, dict):
                items = items.get("item", [])
            if isinstance(items, dict):
                items = [items]
            return total, items
        except (OSError, json.JSONDecodeError) as e:  # 503, 빈 응답, 연결 끊김 등은 잠시 뒤 다시
            last = e
            time.sleep((3, 8, 15, 25, 40)[min(attempt, 4)])
    raise RuntimeError(f"접속 실패({sigungu}-{bjdong}, {page}쪽): {last}")


def fetch_all(key, sigungu, bjdong, since=None, until=None):
    items, page = [], 1
    while True:
        total, got = call(key, sigungu, bjdong, page=page, since=since, until=until)
        items.extend(got)
        if not got or len(items) >= total:
            return total, items
        page += 1
        time.sleep(PAUSE)


# ---------------------------------------------------------------- 코드 찾기

def place_name(item):
    """대지위치에서 동 또는 '읍면 리' 이름을 뽑는다."""
    plc = re.sub(r"\s+", " ", item.get("platPlc") or "")
    m = re.search(r"청원구 ?(\S+[읍면]) ?(\S+리)", plc)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    m = re.search(r"청원구 ?(\S+동)", plc)
    return m.group(1) if m else ""


def probe(key, sigungu, bjdong, failed=None):
    try:
        total, items = call(key, sigungu, bjdong, rows=1)
    except RuntimeError as e:
        print("  확인 실패", bjdong, e)
        if failed is not None:
            failed.append(bjdong)
        return 0, ""
    time.sleep(PAUSE)
    return total, (place_name(items[0]) if items else "")


def name_towns(found):
    """읍·면 자체 코드(예: 25300)는 이름이 없으니 같은 읍·면의 리 이름에서 가져온다."""
    for b, name in list(found.items()):
        if name and not name.isdigit():
            continue
        for other, oname in found.items():
            if other != b and other[:3] == b[:3] and oname and not oname.isdigit():
                found[b] = oname.split()[0]
                break
    return found


def discover(key):
    codes = load(CODES, {"sigungu": "43114", "codes": {}})
    sg = codes["sigungu"]
    found = dict(codes.get("codes", {}))
    failed = []
    print("동 지역 확인 중 (10100~12000)")
    for n in range(10100, 12001, 100):
        b = f"{n:05d}"
        total, name = probe(key, sg, b, failed)
        if total:
            found[b] = name or found.get(b, b)
            print(f"  {b} {found[b]} {total}건")
    print("읍·면 지역 확인 중 (250~399)")
    for p in range(250, 400):
        hit = False
        for r in (0, 21):
            total, _ = probe(key, sg, f"{p}{r:02d}")
            if total:
                hit = True
                break
        if not hit:
            continue
        print(f"  {p}00 읍·면 발견, 리 확인 중")
        for r in range(0, 70):
            b = f"{p}{r:02d}"
            total, name = probe(key, sg, b, failed)
            if total:
                found[b] = name or found.get(b, b)
                print(f"    {b} {found[b]} {total}건")
    if failed:
        print(f"확인 실패 {len(failed)}곳 다시 확인")
        time.sleep(30)
        for b in failed:
            total, name = probe(key, sg, b)
            if total:
                found[b] = name or found.get(b, b)
                print(f"  {b} {found[b]} {total}건")
    codes["codes"] = name_towns(dict(sorted(found.items())))
    codes["discovered"] = now_kst().strftime("%Y-%m-%d")
    save(CODES, codes)
    print(f"코드 {len(found)}개 저장: {CODES}")
    return codes


# ---------------------------------------------------------------- 정리

def ymd(v):
    v = re.sub(r"\D", "", str(v or ""))
    return f"{v[:4]}-{v[4:6]}-{v[6:8]}" if len(v) == 8 and v[:4] > "1900" else ""


def clean(v):
    return re.sub(r"\s+", "", str(v or "")).strip()


def num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0


def toint(v):
    d = re.sub(r"\D", "", str(v or ""))
    return int(d) if d else 0


# 건물 이름에는 개인 이름이 들어 있는 경우가 많다(예: "○○동 1-2 단독주택 (홍길동)").
# 공개 사이트에는 병원·교회·빌딩처럼 기관·시설 이름으로 보이는 것만 남긴다.
PUBLIC_NAME = re.compile(
    r"(병원|의원|교회|성당|사$|사찰|센터|빌딩|타워|학교|대학|재단|관$|청사|창고|공장|아파트|"
    r"오피스텔|호텔|마트|상가|교육|도서관|복지|어린이집|유치원|요양|공원|주민|행정|체육|문화)"
)


def public_name(name, purp, place):
    n = re.sub(r"\([^)]*\)", "", re.sub(r"\s+", " ", str(name or ""))).strip()
    if not n or "주택" in (purp or "") or "씨" in n or (place and n.startswith(place)):
        return ""
    # 이름 칸에 주소를 적은 경우(예: '충청북도 청주시 … 필지 공장')는 이름으로 보지 않는다
    if "청원구 " in n or re.search(r"(번지|필지)", n):
        return ""
    return n if PUBLIC_NAME.search(n) else ""


def normalize(item, names):
    b = str(item.get("bjdongCd") or "").zfill(5)
    bun, ji = toint(item.get("bun")), toint(item.get("ji"))
    place = names.get(b) or place_name(item) or b
    if bun:
        addr = f"{place} {bun}" + (f"-{ji}" if ji else "")
    else:
        blk, lot = clean(item.get("block")), clean(item.get("lot"))
        blk = blk if re.search(r"블[럭록]$", blk) else blk + "블록"
        lot = lot if not lot or re.search(r"(로트|롯트)$", lot) else lot + "롯트"
        addr = f"{place} {blk} {lot}".strip() if clean(item.get("block")) else place
    return {
        "pk": str(item.get("mgmPmsrgstPk") or ""),
        "b": b,
        "place": place,
        "addr": addr,
        "name": public_name(item.get("bldNm"), clean(item.get("mainPurpsCdNm")), place),
        "gb": clean(item.get("archGbCdNm")),
        "purp": clean(item.get("mainPurpsCdNm")),
        "area": num(item.get("totArea")),
        "plat": num(item.get("platArea")),
        "fmly": toint(item.get("fmlyCnt")),
        "hhld": toint(item.get("hhldCnt")),
        "jiyuk": clean(item.get("jiyukCdNm")),
        "jimok": clean(item.get("jimokCdNm")),
        "pms": ymd(item.get("archPmsDay")),
        "sched": ymd(item.get("stcnsSchedDay")),
        "stcns": ymd(item.get("realStcnsDay")),
        "delay": ymd(item.get("stcnsDelayDay")),
        "apr": ymd(item.get("useAprDay")),
        "crtn": ymd(item.get("crtnDay")),
        "ho": toint(item.get("hoCnt")),
        "pnu": make_pnu(item),
    }


def make_pnu(item):
    """필지고유번호(19자리) = 시군구 5 + 법정동 5 + 대지구분 1(일반 1, 산 2) + 본번 4 + 부번 4.
    공시지가를 붙일 때 쓴다. 블록·롯트 주소(platGbCd 2)는 지번이 아니라서 만들지 않는다."""
    sg = re.sub(r"\D", "", str(item.get("sigunguCd") or ""))
    b = re.sub(r"\D", "", str(item.get("bjdongCd") or ""))
    land = {"0": "1", "1": "2"}.get(str(item.get("platGbCd") or "0").strip())
    bun, ji = toint(item.get("bun")), toint(item.get("ji"))
    if len(sg) != 5 or len(b) != 5 or not land or not bun or bun > 9999 or ji > 9999:
        return ""
    return f"{sg}{b}{land}{bun:04d}{ji:04d}"


def merge(store, items, names, today, quiet=False):
    """quiet=True(과거 자료 일괄 받기)면 '새 변화' 기록을 남기지 않는다."""
    recs = store.setdefault("records", {})
    events = store.setdefault("events", [])
    new = changed = 0
    for it in items:
        r = normalize(it, names)
        if not r["pk"]:
            continue
        old = recs.get(r["pk"])
        if old is None:
            r["seen"] = today
            recs[r["pk"]] = r
            new += 1
            if not quiet and r["pms"] and r["pms"] >= "2023-01-01":
                events.append({"d": today, "pk": r["pk"], "t": "new"})
            continue
        r["seen"] = old.get("seen", today)
        for field, t in (("stcns", "stcns"), ("apr", "apr")):
            if not quiet and r[field] and not old.get(field):
                events.append({"d": today, "pk": r["pk"], "t": t})
        if r != old:
            changed += 1
        recs[r["pk"]] = r
    store["events"] = events[-MAX_EVENTS:]
    return new, changed


def update(key, since=None):
    codes = load(CODES, {"sigungu": "43114", "codes": {}})
    if not codes.get("discovered"):
        codes = discover(key)
    store = load(STORE, {"records": {}, "events": []})
    today = now_kst().strftime("%Y-%m-%d")
    quiet = bool(since)  # 날짜를 직접 준 경우 = 과거 자료 일괄 받기
    # 건축HUB 자료는 한 달 단위로 늦게 들어온다(예: 11월 초에 9월 말까지).
    # 새 묶음이 들어올 때 빠뜨리지 않도록, 가지고 있는 가장 최근 변경일 일주일 전부터 다시 받는다.
    if not since:
        latest = max((r.get("crtn", "") for r in store.get("records", {}).values()), default="")
        start = now_kst() - timedelta(days=14)
        if latest:
            start = min(start, datetime.strptime(latest, "%Y-%m-%d").replace(tzinfo=KST) - timedelta(days=7))
        start = max(start, now_kst() - timedelta(days=150))
        since = start.strftime("%Y%m%d")
    names = codes["codes"]
    pending = store.setdefault("pending", {})  # {코드: 다시 받을 시작 날짜}
    tot_new = tot_changed = 0
    failed = []

    if pending:
        print(f"지난번에 못 받은 {len(pending)}곳 다시 받기")
    for b, p_since in sorted(pending.items()):
        try:
            total, items = fetch_all(key, codes["sigungu"], b, since=p_since)
        except RuntimeError as e:
            print("  아직 실패:", b, e)
            continue
        if items and b not in names:
            names[b] = place_name(items[0]) or b
            codes["codes"] = name_towns(dict(sorted(names.items())))
            names = codes["codes"]
            save(CODES, codes)
        n, c = merge(store, items, names, today, quiet=True)
        tot_new += n
        tot_changed += c
        del pending[b]
        print(f"  {b} {names.get(b, b)}: 받은 기록 {len(items)}건 (새 기록 {n})")
        time.sleep(PAUSE)

    for b, name in names.items():
        try:
            total, items = fetch_all(key, codes["sigungu"], b, since=since)
        except RuntimeError as e:
            print("실패:", b, name, e)
            failed.append(b)
            if quiet:  # 과거 자료 받기였다면 그 날짜부터, 아니면 최근 14일부터 다음에 다시
                pending[b] = min(pending.get(b, since), since)
            else:
                pending.setdefault(b, since)
            continue
        n, c = merge(store, items, names, today, quiet=quiet)
        tot_new += n
        tot_changed += c
        print(f"{b} {name}: 받은 기록 {len(items)}건 (새 기록 {n}, 바뀐 기록 {c})")
        time.sleep(PAUSE)
    store["meta"] = {
        "checked": now_kst().strftime("%Y-%m-%d %H:%M"),
        "since": since,
        "failed": failed,
        "source": "국토교통부 건축HUB 건축인허가정보 서비스",
    }
    build(store, codes)  # 이름 정리가 원장에도 반영되도록 저장보다 먼저
    save(STORE, store)
    print(f"완료: 새 기록 {tot_new}, 바뀐 기록 {tot_changed}, 실패 {len(failed)}")
    if failed and len(failed) == len(names):
        sys.exit("모든 지역에서 실패했습니다. 인증키와 접속 상태를 확인하세요.")


# ---------------------------------------------------------------- 필지 위치와 개별공시지가
# 국토교통부 2D데이터 API의 연속지적도(LP_PA_CBND_BUBUN)는 공공누리 제1유형(출처표시)이라
# 필지 경계에서 계산한 중심 좌표와 개별공시지가를 저장해 사이트에 실을 수 있다. 출처는 화면에 적는다.

class FatalVworld(RuntimeError):
    pass


def centroid(geom):
    """다각형(또는 다중 다각형) 중 가장 큰 바깥 고리의 무게중심 (위도, 경도)."""
    if not geom:
        return None
    t, c = geom.get("type"), geom.get("coordinates") or []
    polys = [c] if t == "Polygon" else c if t == "MultiPolygon" else []
    best, best_a = None, -1.0
    for poly in polys:
        if not poly or not poly[0]:
            continue
        ring = poly[0]
        x0, y0 = ring[0][0], ring[0][1]  # 원점을 옮겨 계산해야 소수점 오차가 작다
        pts = [(x - x0, y - y0) for x, y in (q[:2] for q in ring)]
        a = cx = cy = 0.0
        for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
            cr = x1 * y2 - x2 * y1
            a += cr
            cx += (x1 + x2) * cr
            cy += (y1 + y2) * cr
        if abs(a) > 1e-18:
            pt = (y0 + cy / (3 * a), x0 + cx / (3 * a))
        else:
            pt = (sum(p[1] for p in ring) / len(ring), sum(p[0] for p in ring) / len(ring))
        if abs(a) > best_a:
            best, best_a = pt, abs(a)
    return best


def vworld_parcel(key, domain, pnu, tries=4):
    """필지번호 하나를 조회해 [위도, 경도, 개별공시지가(원/㎡), 기준연도]를 돌려준다. 없으면 None."""
    params = {
        "service": "data", "request": "GetFeature", "data": "LP_PA_CBND_BUBUN",
        "key": key, "domain": domain, "attrFilter": "pnu:=:" + pnu,
        "geometry": "true", "attribute": "true", "crs": "EPSG:4326", "format": "json", "size": "5", "page": "1",
    }
    url = VWORLD + "?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dongne-radar-collector", "Referer": domain})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))
            resp = data.get("response") or {}
            st = str(resp.get("status", "")).upper()
            if st == "NOT_FOUND":
                return None
            if st == "ERROR":
                err = resp.get("error") or {}
                code = str(err.get("code", ""))
                if code in ("INCORRECT_KEY", "UNAVAILABLE_KEY", "OVER_REQUEST_LIMIT", "INVALID_KEY", "NOT_AUTHORIZED"):
                    raise FatalVworld(f"브이월드 오류 {code}: {err.get('text', '')}")
                raise OSError(f"{code} {err.get('text', '')}")
            feats = (((resp.get("result") or {}).get("featureCollection") or {}).get("features")) or []
            for f in feats:
                props = f.get("properties") or {}
                if props.get("pnu") and str(props.get("pnu")) != pnu:
                    continue
                pt = centroid(f.get("geometry"))
                if not pt:
                    continue
                return [round(pt[0], 6), round(pt[1], 6), toint(props.get("jiga")), str(props.get("gosi_year") or "")[:4]]
            return None
        except (OSError, json.JSONDecodeError, ValueError) as e:
            last = e
            time.sleep((2, 5, 10, 20)[min(attempt, 3)])
    raise OSError(f"브이월드 접속 실패({pnu}): {last}")


def parcels(limit=9000, workers=4, budget=35 * 60):
    """좌표가 없는 필지만 조회한다. 매일 실행하면 새로 생긴 필지만 받는다.
    한 번에 budget초까지만 받고 저장한다(남은 건 다음 실행에서). 처음 묶음이 모두 실패하면 바로 멈춘다."""
    key = os.environ.get("VWORLD_KEY", "").strip()
    if not key:
        print("VWORLD_KEY가 없어 필지 위치 받기를 건너뜁니다.")
        return
    domain = os.environ.get("VWORLD_DOMAIN", "").strip() or "https://local-rader.github.io"
    store = load(STORE, {"records": {}, "events": []})
    par = load(PARCELS, {"parcels": {}, "missing": {}})
    have, miss = par.setdefault("parcels", {}), par.setdefault("missing", {})
    today = now_kst().strftime("%Y-%m-%d")
    retry_after = (now_kst() - timedelta(days=30)).strftime("%Y-%m-%d")
    want = sorted({r.get("pnu") for r in store.get("records", {}).values()
                   if r.get("pnu") and r.get("pms", "") >= "2023-01-01"}
                  - set(have) - {p for p, d in miss.items() if d > retry_after})
    print(f"필지 위치: 저장된 {len(have)}개, 이번에 받을 최대 {min(len(want), limit)}개 (남은 {len(want)}개)", flush=True)
    want = want[:limit]

    def one(p):
        try:
            return p, "ok", vworld_parcel(key, domain, p, tries=3)
        except FatalVworld as e:
            return p, "fatal", e
        except OSError as e:
            return p, "fail", e

    from concurrent.futures import ThreadPoolExecutor
    t0, done, found, failed, fatal, first_err = time.time(), 0, 0, 0, None, None
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for start in range(0, len(want), 100):
            if time.time() - t0 > budget:
                print(f"  시간 한도({budget // 60}분)에 닿아 여기까지 저장하고 나머지는 다음 실행에서 받습니다.", flush=True)
                break
            chunk = want[start:start + 100]
            res = list(ex.map(one, chunk))
            for p, kind, v in res:
                if kind == "fatal":
                    fatal = v
                elif kind == "fail":
                    failed += 1
                    first_err = first_err or v
                else:
                    done += 1
                    if v:
                        have[p] = v
                        miss.pop(p, None)
                        found += 1
                    else:
                        miss[p] = today
            if fatal:
                break
            if start == 0 and failed == len(chunk):
                fatal = RuntimeError(f"첫 {len(chunk)}개 조회가 모두 실패했습니다. 브이월드 접속이 막혔을 수 있습니다: {first_err}")
                break
            print(f"  {start + len(chunk)}개 조회, 좌표 {found}개, 실패 {failed}개 ({int(time.time() - t0)}초)", flush=True)
            if (start // 100) % 5 == 4:
                save(PARCELS, par)
    par["checked"] = now_kst().strftime("%Y-%m-%d %H:%M")
    save(PARCELS, par)
    print(f"필지 위치 완료: 이번에 {done}개 처리, 좌표 {found}개, 실패 {failed}개, 전체 {len(have)}개", flush=True)
    build(store)
    if fatal:
        sys.exit(str(fatal))


# ---------------------------------------------------------------- 사이트용 데이터

FIELDS = ["pk", "place", "addr", "name", "gb", "purp", "area", "plat", "fmly", "hhld",
          "jiyuk", "jimok", "pms", "sched", "stcns", "delay", "apr", "crtn", "seen",
          "lat", "lng", "jiga", "jy"]
PARCEL_FIELDS = ("lat", "lng", "jiga", "jy")


def build(store=None, codes=None):
    store = store or load(STORE, {"records": {}, "events": []})
    codes = codes or load(CODES, {"codes": {}})
    names = codes.get("codes", {})
    for r in store.get("records", {}).values():  # 예전에 저장된 기록도 같은 기준으로 정리
        r["name"] = public_name(r.get("name"), r.get("purp"), r.get("place"))
        r["addr"] = re.sub(r"\s(로트|롯트)$", "", re.sub(r"(블[럭록])블록", r"\1", re.sub(r"(로트|롯트)롯트", r"\1", r.get("addr", ""))))
        good = names.get(r.get("b", ""), "")
        if r.get("place", "").isdigit() and good and not good.isdigit():
            r["addr"] = r.get("addr", "").replace(r["place"], good, 1)
            r["place"] = good
    rows = [r for r in store.get("records", {}).values() if r.get("pms", "") >= "2023-01-01"]
    rows.sort(key=lambda r: (r.get("pms", ""), r.get("pk", "")), reverse=True)
    keep = {r["pk"] for r in rows}
    # 필지 중심 좌표와 개별공시지가는 parcels.json에서 붙인다 (원장에는 넣지 않음)
    have = load(PARCELS, {}).get("parcels", {})

    def cell(r, f):
        if f in PARCEL_FIELDS:
            v = have.get(r.get("pnu") or "")
            return v[PARCEL_FIELDS.index(f)] if v else ""
        return r.get(f, "")
    out = {
        "meta": dict(store.get("meta", {}), asof=max((r.get("crtn", "") for r in rows), default=""),
                     parcels=sum(1 for r in rows if have.get(r.get("pnu") or ""))),
        "places": sorted({r["place"] for r in rows}),
        "fields": FIELDS,
        "rows": [[cell(r, f) for f in FIELDS] for r in rows],
        "events": [e for e in store.get("events", []) if e["pk"] in keep][-300:],
    }
    with open(SITE, "w", encoding="utf-8") as f:
        f.write("// 자동 생성 파일입니다. 직접 고치지 마세요 (fetch.py build).\n")
        f.write("window.PERMITS = ")
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    print(f"data.js 저장: 2023년 이후 허가 {len(rows)}건")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "update"
    since = None
    if "--since" in argv:
        since = re.sub(r"\D", "", argv[argv.index("--since") + 1])
    if cmd == "discover":
        discover(api_key())
    elif cmd == "update":
        update(api_key(), since)
    elif cmd == "parcels":
        parcels()
    elif cmd == "build":
        store = load(STORE, {"records": {}, "events": []})
        build(store)
        save(STORE, store)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
