#!/usr/bin/env python3
"""청주 부동산 실거래가 수집기 (국토교통부 실거래가 공개 자료, 공공데이터포털).

  python trades.py update   최근 3달을 다시 받고, 비어 있는 달(처음이면 36달)을 채운 뒤 trades.js를 만든다
  python trades.py build    trades.json으로 trades.js·trades-summary.json만 다시 만든다

인증키: 환경변수 RTMS_KEY (없으면 DATA_GO_KR_KEY). 인코딩된 키를 넣어도 된다.

- 계약일 기준 자료다. 거래 신고 기한이 30일이라 최근 한 달은 아직 덜 들어와 있다 → '집계 중'으로 표시.
- 해제(취소)된 거래는 뺀다. 늦게 들어온 신고와 해제를 반영하려고 최근 3달은 매번 다시 받는다.
- 아파트·오피스텔 말고는 지번 일부가 가려져(예: 2***) 나온다. 그대로 둔다.
"""
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "trades.json")
SITE = os.path.join(HERE, "trades.js")
SUMMARY = os.path.join(HERE, "trades-summary.json")  # data.js(홈 브리핑)와 ai-context.json이 읽는 작은 요약
CODES = os.path.join(HERE, "codes.json")
API = "https://apis.data.go.kr/1613000/"
KST = timezone(timedelta(hours=9))
AREAS = [("43111", "상당구"), ("43112", "서원구"), ("43113", "흥덕구"), ("43114", "청원구")]
# (코드, 이름, 주소, ㎡당 가격에 쓰는 면적 항목, 면적 이름)
KINDS = [
    ("apt", "아파트", "RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev", ("excluUseAr",), "전용면적"),
    ("rh", "연립·다세대", "RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade", ("excluUseAr",), "전용면적"),
    ("sh", "단독·다가구", "RTMSDataSvcSHTrade/getRTMSDataSvcSHTrade", ("totalFloorAr",), "연면적"),
    ("offi", "오피스텔", "RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade", ("excluUseAr",), "전용면적"),
    ("land", "토지", "RTMSDataSvcLandTrade/getRTMSDataSvcLandTrade", ("dealArea",), "토지면적"),
    ("nrg", "상업·업무용", "RTMSDataSvcNrgTrade/getRTMSDataSvcNrgTrade", ("buildingAr", "plottageAr"), "건물면적"),
    ("indu", "공장·창고", "RTMSDataSvcInduTrade/getRTMSDataSvcInduTrade", ("buildingAr", "plottageAr"), "건물면적"),
]
FIRST_MONTHS = 36   # 처음 받을 기간, 그리고 보관 기간
REFRESH_MONTHS = 3  # 매번 다시 받는 최근 달 수
DETAIL_MONTHS = 12  # 사이트 목록에 싣는 기간
PAUSE = 0.2
BUDGET = 45 * 60
# 저장하는 한 줄: [계약일, 동네, 금액(만원), 면적㎡, 이름(단지), 층, 건축년도, 구분(주택유형·지목/용도지역·건물용도), 지번, 직거래(1/0), 지분거래(1/0)]
ROW = ["date", "place", "amt", "area", "name", "floor", "by", "extra", "jibun", "direct", "share"]


def now_kst():
    return datetime.now(KST)


def load(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save(path, obj, compact=False):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        else:
            json.dump(obj, f, ensure_ascii=False, indent=0, sort_keys=True)
    os.replace(tmp, path)


def api_key():
    key = (os.environ.get("RTMS_KEY") or os.environ.get("DATA_GO_KR_KEY") or "").strip()
    if not key:
        sys.exit("인증키가 없습니다. 환경변수 RTMS_KEY 를 설정하세요 (GitHub에서는 Secrets).")
    return urllib.parse.unquote(key) if "%" in key else key


def month_add(ym, k):
    y, m = int(ym[:4]), int(ym[5:7]) + k
    while m < 1:
        y, m = y - 1, m + 12
    while m > 12:
        y, m = y + 1, m - 12
    return f"{y:04d}-{m:02d}"


def months_back(n, today=None):
    cur = (today or now_kst()).strftime("%Y-%m")
    return [month_add(cur, -i) for i in range(n)]


def complete_month(today=None):
    """신고 기한(30일)이 지나 다 들어온 것으로 보는 마지막 달."""
    t = (today or now_kst()).date()
    ym = t.strftime("%Y-%m")
    while True:
        end = datetime.strptime(month_add(ym, 1) + "-01", "%Y-%m-%d").date() - timedelta(days=1)
        if end + timedelta(days=30) <= t:
            return ym
        ym = month_add(ym, -1)


class NotRegistered(Exception):
    pass


def call(key, path, sg, ym, page=1, rows=1000, tries=4):
    q = urllib.parse.urlencode({"serviceKey": key, "LAWD_CD": sg, "DEAL_YMD": ym.replace("-", ""), "pageNo": page, "numOfRows": rows})
    url = API + path + "?" + q
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "turnoko-collector"})
            with urllib.request.urlopen(req, timeout=40) as r:
                body = r.read().decode("utf-8", "replace")
            if "SERVICE_KEY_IS_NOT_REGISTERED" in body:
                raise NotRegistered()
            root = ET.fromstring(body)
            code = (root.findtext(".//resultCode") or "").strip()
            if code not in ("000", "00"):
                raise RuntimeError(f"resultCode {code} {root.findtext('.//resultMsg')}")
            total = int(root.findtext(".//totalCount") or 0)
            items = [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]
            return total, items
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if "SERVICE_KEY_IS_NOT_REGISTERED" in body:
                raise NotRegistered()
            last = f"HTTP {e.code}"
        except NotRegistered:
            raise
        except Exception as e:  # 잠깐 끊김 등
            last = str(e)[:120]
        time.sleep(2 + 3 * i)
    raise RuntimeError(last or "알 수 없는 오류")


def fetch_month(key, path, sg, ym):
    out, page = [], 1
    while True:
        total, items = call(key, path, sg, ym, page)
        out += items
        if not items or len(out) >= total or page >= 20:
            return out
        page += 1
        time.sleep(PAUSE)


def tofloat(v):
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return 0.0


def norm(kind, it, names):
    g = lambda k: (it.get(k) or "").strip()
    if g("cdealType") or g("cdealDay"):  # 해제된 거래
        return None
    try:
        date = f"{int(g('dealYear')):04d}-{int(g('dealMonth')):02d}-{int(g('dealDay')):02d}"
    except ValueError:
        return None
    amt = int(tofloat(g("dealAmount")))
    if amt <= 0:
        return None
    place = names.get(g("umdCd"), "") if g("umdCd") else ""
    place = place or re.sub(r"\s+", " ", g("umdNm"))
    spec = next(k for k in KINDS if k[0] == kind)
    area = next((tofloat(g(f)) for f in spec[3] if tofloat(g(f)) > 0), 0.0)
    name = g("aptNm") or g("offiNm") or g("mhouseNm") or ""
    if kind == "sh":
        extra = g("houseType")
    elif kind == "land":
        extra = " · ".join(x for x in (g("jimok"), g("landUse")) if x)
    elif kind in ("nrg", "indu"):
        extra = " · ".join(x for x in (g("buildingUse"), g("landUse")) if x)
    else:
        extra = ""
    floor = g("floor")
    return [date, place, amt, round(area, 2), name, floor, g("buildYear"), extra, g("jibun"),
            1 if "직거래" in g("dealingGbn") else 0, 1 if g("shareDealingType") else 0]


def town_names():
    """법정동 코드(5자리) → '오창읍 양청리' 같은 이름. 아파트 자료의 동네 이름을 사이트와 맞춘다."""
    codes = load(CODES, {"areas": {}})
    return {sg: a.get("codes", {}) for sg, a in codes.get("areas", {}).items()}


def update(key):
    t0 = time.time()
    store = load(STORE, {"meta": {}, "data": {}, "got": {}})
    names = town_names()
    window = months_back(FIRST_MONTHS)
    recent = set(months_back(REFRESH_MONTHS))
    status = {}
    jobs = []
    for kind, label, path, _, _ in KINDS:
        for sg, gu in AREAS:
            for ym in window:  # 최근 달 먼저, 그다음 비어 있는 달
                k = f"{kind}|{sg}|{ym}"
                if ym in recent or k not in store["got"]:
                    jobs.append((0 if ym in recent else 1, kind, label, path, sg, ym, k))
    jobs.sort(key=lambda j: (j[0], j[1], j[4], j[5]), reverse=False)
    blocked, done, n_rows = set(), 0, 0
    for _, kind, label, path, sg, ym, k in jobs:
        if kind in blocked:
            continue
        if time.time() - t0 > BUDGET:
            print("시간 한도에 닿아 여기까지 저장하고 다음 실행 때 이어 받습니다.")
            break
        try:
            items = fetch_month(key, path, sg, ym)
        except NotRegistered:
            blocked.add(kind)
            status[kind] = "not_registered"
            print(f"{label}: 인증키가 아직 등록되지 않았습니다 (활용신청 후 1~2시간 걸릴 수 있음). 건너뜁니다.")
            continue
        except Exception as e:
            status.setdefault(kind, f"error: {e}")
            print(f"{label} {sg} {ym}: 실패 {e}")
            continue
        rows = [r for r in (norm(kind, it, names.get(sg, {})) for it in items) if r]
        rows.sort(key=lambda r: (r[0], r[1], r[2]))
        store["data"][k] = rows
        store["got"][k] = now_kst().strftime("%Y-%m-%d")
        status.setdefault(kind, "ok")
        done += 1
        n_rows += len(rows)
        if done % 40 == 0:
            save(STORE, store)
            print(f"  {done}건 요청 완료, 거래 {n_rows}건")
        time.sleep(PAUSE)
    # 보관 기간이 지난 달은 지운다
    keep = set(window)
    for k in list(store["data"].keys()):
        if k.split("|")[2] not in keep:
            store["data"].pop(k, None)
            store["got"].pop(k, None)
    store["meta"] = {"fetched": now_kst().strftime("%Y-%m-%d %H:%M"), "status": status,
                     "source": "국토교통부 실거래가 공개 자료 (공공데이터포털)"}
    save(STORE, store)
    print(f"trades.json 저장: 요청 {done}건, 이번에 받은 거래 {n_rows}건, 상태 {status}")
    build(store)


# ---------------------------------------------------------------- 사이트용 자료

def _median(xs):
    return statistics.median(xs) if xs else None


def _area1(place):
    return (place or "").split(" ")[0]


def build(store=None):
    store = store or load(STORE, {"meta": {}, "data": {}, "got": {}})
    gu_of = dict(AREAS)
    today = now_kst()
    full = complete_month(today)
    detail_from = month_add(today.strftime("%Y-%m"), -(DETAIL_MONTHS - 1))
    kinds = [k[0] for k in KINDS]
    have = sorted({k.split("|")[0] for k in store["data"]}, key=kinds.index)
    # 모든 거래를 (종류, 구, 행)으로 펼친다
    flat = []
    for k, rows in store["data"].items():
        kind, sg, _ = k.split("|")
        for r in rows:
            flat.append((kind, sg, r))
    asof = max((r[0] for _, _, r in flat), default="")

    # 월별 합계: 종류 → 지역(all·구·동네) → 'YYMM' → [건수, ㎡당 중앙값(만원), 금액 중앙값(만원)]
    buckets = {}
    for kind, sg, r in flat:
        ym = r[0][:7]
        for p in ("all", gu_of.get(sg, sg), _area1(r[1])):
            if not p:
                continue
            b = buckets.setdefault(kind, {}).setdefault(p, {}).setdefault(ym, [0, [], []])
            b[0] += 1
            b[2].append(r[2])
            if r[3] > 0 and not r[10]:  # 지분 거래는 ㎡당 가격에서 뺀다
                b[1].append(r[2] / r[3])
    agg = {}
    for kind, places in buckets.items():
        for p, months in places.items():
            for ym, (n, per, amt) in months.items():
                m = _median(per)
                agg.setdefault(kind, {}).setdefault(p, {})[ym[2:4] + ym[5:7]] = [n, round(m, 1) if m else None, round(_median(amt))]

    # 최근 12달 상세 (사전으로 줄여서)
    pdict, ndict, edict = {}, {}, {}

    def idx(d, v):
        if v not in d:
            d[v] = len(d)
        return d[v]
    rows_out = []
    for kind, sg, r in sorted(flat, key=lambda x: x[2][0], reverse=True):
        if r[0][:7] < detail_from:
            continue
        rows_out.append([kinds.index(kind), idx(pdict, gu_of.get(sg, sg) + "|" + r[1]), r[0][2:].replace("-", ""), r[2], r[3],
                         idx(ndict, r[4]), r[5], r[6], idx(edict, r[7]), r[8], r[9], r[10]])
    site = {
        "meta": {"fetched": store["meta"].get("fetched", ""), "asof": asof, "complete": full, "source": store["meta"].get("source", ""),
                 "status": store["meta"].get("status", {}), "kinds": [[k[0], k[1], k[4]] for k in KINDS if k[0] in have],
                 "window": [months_back(FIRST_MONTHS, today)[-1], today.strftime("%Y-%m")], "detailFrom": detail_from},
        "agg": agg,
        "places": list(pdict.keys()), "names": list(ndict.keys()), "extras": list(edict.keys()),
        "rowFields": ["kind", "gu|place", "yymmdd", "amt", "area", "name", "floor", "buildYear", "extra", "jibun", "direct", "share"],
        "rows": rows_out,
    }
    with open(SITE, "w", encoding="utf-8") as f:
        f.write("// 자동 생성 파일입니다. 직접 고치지 마세요 (trades.py build).\n")
        f.write("window.TRADES = ")
        json.dump(site, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    summary(agg, full, store)
    print(f"trades.js 저장: 최근 {DETAIL_MONTHS}달 거래 {len(rows_out)}건, 기준 {asof}, 다 들어온 달 {full}")


def summary(agg, full, store):
    """홈 브리핑과 AI 질문이 쓰는 작은 요약. 다 들어온 달까지만 센다."""
    labels = {k[0]: k[1] for k in KINDS}
    last12 = [month_add(full, -i) for i in range(12)]
    prev12 = [month_add(full, -12 - i) for i in range(12)]

    def ymk(ym):
        return ym[2:4] + ym[5:7]

    def pack(months_map, mlist):
        n = sum(months_map.get(ymk(m), [0])[0] for m in mlist)
        per = [months_map[ymk(m)][1] for m in mlist if ymk(m) in months_map and months_map[ymk(m)][1]]
        return n, per
    places = {}
    for kind, pm in agg.items():
        for p, mm in pm.items():
            n12, per = pack(mm, last12)
            p12, _ = pack(mm, prev12)
            per_all = []
            for m in last12:
                v = mm.get(ymk(m))
                if v and v[1]:
                    per_all += [v[1]] * v[0]  # 달마다 중앙값을 건수만큼 반복 (근사)
            places.setdefault(p, {})[labels[kind]] = {
                "최근12달거래": n12, "그전12달거래": p12,
                "㎡당가격중앙값만원_근사": round(_median(per_all), 1) if per_all else None,
                "월별거래": {m: mm.get(ymk(m), [0])[0] for m in (month_add(full, -2), month_add(full, -1), full)},
            }
    brief = {}
    for kind in ("apt", "land", "sh"):
        for p, mm in agg.get(kind, {}).items():
            brief.setdefault(p, {})[kind] = [mm.get(ymk(m), [0])[0] for m in (full, month_add(full, -1), month_add(full, -12))]
    out = {"complete": full, "fetched": store["meta"].get("fetched", ""), "places": places,
           "brief": {"month": full, "labels": {"apt": "아파트", "land": "토지", "sh": "단독·다가구"}, "places": brief},
           "기준": f"계약일 기준, {full}까지(신고 기한 30일이 지난 달). 해제된 거래 제외. ㎡당 가격은 달별 중앙값을 거래 수로 묶은 근사값."}
    save(SUMMARY, out, compact=True)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "update"
    if cmd == "update":
        update(api_key())
    elif cmd == "build":
        build()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
