"""건축물대장(표제부)으로 청주 건물의 사용승인 연도를 법정동·리별로 모은다 → aging.json

사용법
  python aging.py probe    # 인증키로 건축물대장 API가 되는지, 응답 항목 이름 확인
  python aging.py update   # 오래 전에 받은 동네부터 이어서 받기 (한 번에 BUDGET초까지)
  python aging.py build    # 저장된 집계로 aging.json만 다시 만들기

인증키: 환경변수 DATA_GO_KR_KEY (공공데이터포털 '국토교통부_건축HUB_건축물대장정보 서비스' 활용신청 필요)
건물 하나하나의 기록은 저장하지 않고, 동네별 연도 집계와 20세대 이상 공동주택 단지 목록만 남긴다.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

# 도시 선택: TURNOKO_CITY=충주 로 실행하면 충주 자료를 chungju/ 폴더에 따로 저장 (기본 청주)
CITYK = os.environ.get("TURNOKO_CITY", "청주").strip() or "청주"
CITY_DIR = {"청주": "", "충주": "chungju"}[CITYK]
CITY_BBOX = {"청주": (36.38, 36.86, 127.22, 127.78), "충주": (36.78, 37.23, 127.63, 128.15)}[CITYK]
CODES = os.path.join(HERE, CITY_DIR, "codes.json")
PARCELS = os.path.join(HERE, "parcels.json")
STORE = os.path.join(HERE, CITY_DIR, "aging-store.json")   # 동네별 집계 (이어 받기용)
OUT = os.path.join(HERE, CITY_DIR, "aging.json")           # 사이트가 읽는 파일
BASES = [
    "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo",
    "https://apis.data.go.kr/1613000/BldRgstService_v2/getBrTitleInfo",
]
KST = timezone(timedelta(hours=9))
PAUSE = 0.2
BUDGET = int(os.environ.get("AGING_BUDGET", 60 * 60))
REFRESH_DAYS = 30
MIN_HHLD = 20


def now():
    return datetime.now(KST)


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def key():
    k = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if not k:
        sys.exit("DATA_GO_KR_KEY가 없습니다.")
    return k


def call(base, k, sg, bj, page=1, rows=100, tries=4):
    q = {"serviceKey": k, "sigunguCd": sg, "bjdongCd": bj, "numOfRows": rows, "pageNo": page, "_type": "json"}
    url = base + "?" + urllib.parse.urlencode(q)
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "turnoko-aging"})
            with urllib.request.urlopen(req, timeout=20) as r:
                text = r.read().decode("utf-8", errors="replace")
            if text.lstrip().startswith("<"):
                m = re.search(r"<returnAuthMsg>(.*?)</returnAuthMsg>|<resultMsg>(.*?)</resultMsg>|<returnReasonCode>(.*?)</returnReasonCode>", text)
                raise PermissionError(m.group(0) if m else text[:300])
            data = json.loads(text)
            head = data.get("response", {}).get("header", {})
            if str(head.get("resultCode", "00")) not in ("00", "0"):
                raise PermissionError(str(head))
            body = data["response"].get("body", {}) or {}
            items = body.get("items") or []
            if isinstance(items, dict):
                items = items.get("item", [])
            if isinstance(items, dict):
                items = [items]
            return int(body.get("totalCount") or 0), items
        except PermissionError:
            raise
        except (OSError, ValueError) as e:
            last = e
            time.sleep((3, 8, 15, 25)[min(i, 3)])
    raise RuntimeError(f"접속 실패 {sg}-{bj} {page}쪽: {last}")


def areas():
    c = load(CODES, {}).get("areas", {})
    out = []
    for sg, a in sorted(c.items()):
        for bj, name in sorted(a.get("codes", {}).items()):
            out.append((sg, bj, a.get("gu", ""), name))
    return out


# 용도 묶음: 단독주택 / 공동주택 / 그 외
def kind(purp):
    p = purp or ""
    if "공동주택" in p or p in ("아파트", "연립주택", "다세대주택", "기숙사"):
        return "apt"
    if "단독주택" in p or p in ("다가구주택", "다중주택", "공관"):
        return "house"
    return "etc"


def year(v):
    v = str(v or "").strip()
    if len(v) >= 4 and v[:4].isdigit():
        y = int(v[:4])
        if 1900 <= y <= now().year:
            return y
    return None


def base_name(n):
    n = (n or "").strip()
    n = re.sub(r"\s*(제?\s*\d+\s*동|[A-Z가-힣]?\d*동)$", "", n).strip()  # '○○아파트 101동' → '○○아파트'
    return n


def summarize(items, sg, bj):
    """한 동네의 표제부 기록 → 연도 집계와 공동주택 단지 목록"""
    yrs = {"house": Counter(), "apt": Counter(), "etc": Counter()}
    unknown = 0
    sites = defaultdict(lambda: {"names": Counter(), "years": [], "hhld": 0, "dongs": 0, "addr": ""})
    for it in items:
        if str(it.get("mainAtchGbCd", "0")).strip() not in ("0", ""):
            continue  # 부속건축물(창고·화장실 등)은 뺀다
        k = kind(it.get("mainPurpsCdNm"))
        y = year(it.get("useAprDay"))
        if y is None:
            unknown += 1
        else:
            yrs[k][y] += 1
        if k == "apt":
            mt = "2" if str(it.get("platGbCd", "0")).strip() == "1" else "1"
            s = sites[(mt, str(it.get("bun", "")).strip(), str(it.get("ji", "")).strip())]
            nm = base_name(it.get("bldNm"))
            if nm:
                s["names"][nm] += 1
            if y:
                s["years"].append(y)
            try:
                s["hhld"] += int(float(it.get("hhldCnt") or 0))
            except ValueError:
                pass
            s["dongs"] += 1
            s["addr"] = s["addr"] or (it.get("platPlc") or "").strip()
    complexes = []
    for (mt, bun, ji), s in sites.items():
        if s["hhld"] < MIN_HHLD or not s["years"]:
            continue
        complexes.append({
            "name": s["names"].most_common(1)[0][0] if s["names"] else "",
            "addr": s["addr"], "y": min(s["years"]), "y2": max(s["years"]),
            "hh": s["hhld"], "dongs": s["dongs"],
            "pnu": sg + bj + mt + bun.zfill(4)[-4:] + ji.zfill(4)[-4:] if bun.isdigit() else "",
        })
    return {"y": {k: {str(a): b for a, b in sorted(v.items())} for k, v in yrs.items()},
            "unknown": unknown, "complexes": complexes}


def fetch_area(base, k, sg, bj, deadline):
    items, page, total = [], 1, None
    while True:
        if time.time() > deadline:
            return None
        total, got = call(base, k, sg, bj, page=page)
        items.extend(got)
        if not got or len(items) >= total:
            return items
        page += 1
        time.sleep(PAUSE)


def pick_base(k):
    sg, bj = "43114", "10100"  # 청원구 우암동으로 시험
    errs = []
    for b in BASES:
        try:
            total, items = call(b, k, sg, bj, rows=3)
            return b, total, items
        except Exception as e:  # noqa: BLE001
            errs.append(f"{b}: {e}")
    raise SystemExit("건축물대장 API에 접속하지 못했습니다.\n" + "\n".join(errs))


def probe():
    b, total, items = pick_base(key())
    print("사용 주소:", b)
    print("우암동 표제부 전체 건수:", total)
    if items:
        print("응답 항목:", ", ".join(sorted(items[0].keys())))
        for it in items:
            print({x: it.get(x) for x in ("platPlc", "bldNm", "mainPurpsCdNm", "mainAtchGbCd", "useAprDay", "hhldCnt", "strctCdNm")})


def update():
    k = key()
    b, _, _ = pick_base(k)
    store = load(STORE, {"areas": {}})
    today = now().strftime("%Y-%m-%d")
    stale = (now() - timedelta(days=REFRESH_DAYS)).strftime("%Y-%m-%d")
    todo = [a for a in areas() if store["areas"].get(a[0] + a[1], {}).get("got", "") < stale]
    todo.sort(key=lambda a: store["areas"].get(a[0] + a[1], {}).get("got", ""))
    print(f"받을 동네 {len(todo)}곳 / 전체 {len(areas())}곳")
    deadline = time.time() + BUDGET
    done = 0
    for sg, bj, gu, name in todo:
        try:
            items = fetch_area(b, k, sg, bj, deadline)
        except RuntimeError as e:
            print("건너뜀:", e)
            continue
        if items is None:
            print("시간이 다 돼서 다음 실행 때 이어 받습니다.")
            break
        s = summarize(items, sg, bj)
        s.update({"gu": gu, "place": name, "got": today, "raw": len(items)})
        store["areas"][sg + bj] = s
        done += 1
        if done % 10 == 0:
            save(STORE, store)
            print(f"  {done}곳 저장")
    store["checked"] = now().strftime("%Y-%m-%d %H:%M")
    save(STORE, store)
    print(f"이번에 받은 동네 {done}곳")
    build(store)


def centroids():
    """parcels.json 필지 좌표의 평균으로 동네 대표 위치를 잡는다 (경계선이 아니라 참고용 점)."""
    p = load(PARCELS, {}).get("parcels", {})
    acc = defaultdict(lambda: [0.0, 0.0, 0])
    for pnu, v in p.items():
        if not v or v[0] is None:
            continue
        a = acc[pnu[:10]]
        a[0] += v[0]; a[1] += v[1]; a[2] += 1
    return {k: [round(a[0] / a[2], 5), round(a[1] / a[2], 5)] for k, a in acc.items() if a[2]}


def build(store=None):
    store = store or load(STORE, {"areas": {}})
    cen = centroids()
    places, complexes = [], []
    for code, s in sorted(store["areas"].items()):
        places.append({"code": code, "gu": s.get("gu", ""), "place": s.get("place", ""),
                       "y": s["y"], "unknown": s.get("unknown", 0), "at": cen.get(code)})
        for c in s.get("complexes", []):
            c = dict(c, gu=s.get("gu", ""), place=s.get("place", ""))
            complexes.append(c)
    # 한 단지가 여러 지번에 걸친 경우(같은 동네·같은 이름·같은 사용승인 연도)는 하나로 합친다
    merged = {}
    for c in complexes:
        k = (c["gu"], c["place"], c["name"], c["y"]) if c["name"] else (c["gu"], c["place"], c["addr"], c["y"])
        m = merged.get(k)
        if m:
            m["hh"] += c["hh"]; m["dongs"] += c["dongs"]; m["y2"] = max(m["y2"], c["y2"]); m["lots"] += 1
        else:
            merged[k] = dict(c, lots=1)
    complexes = sorted(merged.values(), key=lambda c: (c["y"], -c["hh"]))
    got = sorted(s.get("got", "") for s in store["areas"].values())
    out = {
        "meta": {
            "source": "국토교통부 건축HUB 건축물대장정보 (표제부)",
            "checked": store.get("checked", ""), "oldest": got[0] if got else "", "newest": got[-1] if got else "",
            "areas": len(places), "total_areas": len(areas()),
            "note": "주건축물만 셈(부속건축물 제외). 연도는 사용승인일 기준. 공동주택 단지는 같은 지번의 공동주택 표제부를 묶은 것으로 20세대 이상만.",
        },
        "places": places, "complexes": complexes,
    }
    save(OUT, out)
    n = sum(sum(sum(v.values()) for v in p["y"].values()) for p in places)
    print(f"aging.json: 동네 {len(places)}곳, 연도 있는 건물 {n:,}동, 공동주택 단지 {len(complexes)}곳")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "update"
    {"probe": probe, "update": update, "build": build}[cmd]()
