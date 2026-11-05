"""
동네레이더 데이터 수집기

건축HUB 건축인허가정보(국토교통부) API에서 청주시 청원구의 인허가 기록을 받아
permits.json(원장)에 합치고, 웹사이트가 읽는 data.js를 만든다.

사용법
  python fetch.py discover            청원구 법정동·리 코드를 자동으로 찾아 codes.json에 저장
  python fetch.py update              최근 14일 사이 바뀐 기록을 받아 합치기 (매일 자동 실행용)
  python fetch.py update --since 20230101   그 날짜 이후 바뀐 기록을 모두 받기 (처음 한 번)
  python fetch.py build               permits.json으로 data.js만 다시 만들기

인증키는 환경변수 DATA_GO_KR_KEY 로 받는다. 코드에 직접 적지 않는다.
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


def call(key, sigungu, bjdong, page=1, rows=100, since=None, until=None, tries=3):
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
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
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


def probe(key, sigungu, bjdong):
    try:
        total, items = call(key, sigungu, bjdong, rows=1)
    except RuntimeError as e:
        print("  확인 실패", bjdong, e)
        return 0, ""
    time.sleep(PAUSE)
    return total, (place_name(items[0]) if items else "")


def discover(key):
    codes = load(CODES, {"sigungu": "43114", "codes": {}})
    sg = codes["sigungu"]
    found = dict(codes.get("codes", {}))
    print("동 지역 확인 중 (10100~12000)")
    for n in range(10100, 12001, 100):
        b = f"{n:05d}"
        total, name = probe(key, sg, b)
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
            total, name = probe(key, sg, b)
            if total:
                found[b] = name or found.get(b, b)
                print(f"    {b} {found[b]} {total}건")
    codes["codes"] = dict(sorted(found.items()))
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
    return n if PUBLIC_NAME.search(n) else ""


def normalize(item, names):
    b = str(item.get("bjdongCd") or "").zfill(5)
    bun, ji = toint(item.get("bun")), toint(item.get("ji"))
    place = names.get(b) or place_name(item) or b
    if bun:
        addr = f"{place} {bun}" + (f"-{ji}" if ji else "")
    else:
        blk, lot = clean(item.get("block")), clean(item.get("lot"))
        addr = f"{place} {blk}블록 {lot}롯트".strip() if blk else place
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
        "apr": ymd(item.get("useAprDay")),
        "crtn": ymd(item.get("crtnDay")),
    }


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
    since = since or (now_kst() - timedelta(days=14)).strftime("%Y%m%d")
    names = codes["codes"]
    tot_new = tot_changed = 0
    failed = []
    for b, name in names.items():
        try:
            total, items = fetch_all(key, codes["sigungu"], b, since=since)
        except RuntimeError as e:
            print("실패:", b, name, e)
            failed.append(b)
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


# ---------------------------------------------------------------- 사이트용 데이터

FIELDS = ["pk", "place", "addr", "name", "gb", "purp", "area", "plat", "fmly", "hhld",
          "jiyuk", "jimok", "pms", "sched", "stcns", "apr", "crtn", "seen"]


def build(store=None, codes=None):
    store = store or load(STORE, {"records": {}, "events": []})
    codes = codes or load(CODES, {"codes": {}})
    for r in store.get("records", {}).values():  # 예전에 저장된 이름도 같은 기준으로 정리
        r["name"] = public_name(r.get("name"), r.get("purp"), r.get("place"))
    rows = [r for r in store.get("records", {}).values() if r.get("pms", "") >= "2023-01-01"]
    rows.sort(key=lambda r: (r.get("pms", ""), r.get("pk", "")), reverse=True)
    keep = {r["pk"] for r in rows}
    out = {
        "meta": store.get("meta", {}),
        "places": sorted({r["place"] for r in rows}),
        "fields": FIELDS,
        "rows": [[r.get(f, "") for f in FIELDS] for r in rows],
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
    elif cmd == "build":
        store = load(STORE, {"records": {}, "events": []})
        build(store)
        save(STORE, store)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
