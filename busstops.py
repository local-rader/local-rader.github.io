"""청주 시내버스 정류소 위치를 받는다 → busstops.json

국토교통부(TAGO) 버스정류소정보 서비스. 인증키는 DATA_GO_KR_KEY
(공공데이터포털 '국토교통부_(TAGO)_버스정류소정보' 활용신청 필요).
  python busstops.py probe   # 접속 확인, 청주 도시코드 찾기
  python busstops.py update  # 정류소 전체 받기
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

# 도시 선택: TURNOKO_CITY=충주 로 실행하면 충주 자료를 chungju/ 폴더에 따로 저장 (기본 청주)
CITYK = os.environ.get("TURNOKO_CITY", "청주").strip() or "청주"
CITY_DIR = {"청주": "", "충주": "chungju"}[CITYK]
CITY_BBOX = {"청주": (36.38, 36.86, 127.22, 127.78), "충주": (36.78, 37.23, 127.63, 128.15)}[CITYK]
OUT = os.path.join(HERE, CITY_DIR, "busstops.json")
BASE = "https://apis.data.go.kr/1613000/BusSttnInfoInqireService"
KST = timezone(timedelta(hours=9))


def key():
    k = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if not k:
        sys.exit("DATA_GO_KR_KEY가 없습니다.")
    return k


def get(op, **q):
    q.update({"serviceKey": key(), "_type": "json"})
    url = f"{BASE}/{op}?" + urllib.parse.urlencode(q)
    last = None
    for i in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "turnoko-busstops"})
            with urllib.request.urlopen(req, timeout=20) as r:
                text = r.read().decode("utf-8", errors="replace")
            if text.lstrip().startswith("<"):
                raise PermissionError(text[:300])
            body = json.loads(text)["response"]["body"]
            items = (body.get("items") or {})
            items = items.get("item", []) if isinstance(items, dict) else items
            if isinstance(items, dict):
                items = [items]
            return int(body.get("totalCount") or 0), items
        except PermissionError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep((3, 8, 15, 25)[i])
    raise RuntimeError(f"{op} 접속 실패: {last}")


def city_code():
    _, items = get("getCtyCodeList")
    for it in items:
        if CITYK in str(it.get("cityname", "")):
            return str(it["citycode"]), it["cityname"]
    raise SystemExit("도시 목록에 청주가 없습니다: " + ", ".join(str(i.get("cityname")) for i in items[:50]))


def probe():
    code, name = city_code()
    total, items = get("getSttnNoList", cityCode=code, numOfRows=3, pageNo=1)
    print(f"도시코드 {code} ({name}), 정류소 {total}곳")
    for it in items:
        print(it)


def update():
    code, name = city_code()
    stops, page = [], 1
    while True:
        total, items = get("getSttnNoList", cityCode=code, numOfRows=1000, pageNo=page)
        for it in items:
            try:
                lat, lng = float(it["gpslati"]), float(it["gpslong"])
            except (KeyError, TypeError, ValueError):
                continue
            stops.append([str(it.get("nodenm", "")).strip(), round(lat, 6), round(lng, 6), str(it.get("nodeno", "") or "")])
        if not items or len(stops) >= total or page * 1000 >= total:
            break
        page += 1
        time.sleep(0.3)
    if len(stops) < 100:
        sys.exit(f"정류소가 {len(stops)}곳뿐이라 저장하지 않습니다.")
    out = {"meta": {"source": "국토교통부 (TAGO) 버스정류소정보", "city": name,
                    "got": datetime.now(KST).strftime("%Y-%m-%d"), "count": len(stops)},
           "fields": ["name", "lat", "lng", "no"], "stops": stops}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"busstops.json: {len(stops)}곳")


if __name__ == "__main__":
    {"probe": probe, "update": update}[sys.argv[1] if len(sys.argv) > 1 else "update"]()
