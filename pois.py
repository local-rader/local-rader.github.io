"""청주 공원·도서관 위치를 공공데이터 표준데이터에서 받는다 → pois.json (생활권 분석용)

  python pois.py probe    # 접속 확인, 응답 항목 이름
  python pois.py update
인증키: DATA_GO_KR_KEY. 공공데이터포털에서 아래 두 표준데이터의 '오픈API' 활용신청이 필요하다.
  전국도시공원정보표준데이터  https://www.data.go.kr/data/15012890/standard.do
  전국도서관표준데이터        https://www.data.go.kr/data/15013109/standard.do
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "pois.json")
KST = timezone(timedelta(hours=9))
SETS = {
    "park": (["https://api.data.go.kr/openapi/tn_pubr_public_cty_park_info_api"], ("parkNm",), ("parkSe",)),
    "lib": (["https://api.data.go.kr/openapi/tn_pubr_public_lbrry_api"], ("lbrryNm",), ("lbrrySe",)),
    # 전국전통시장표준데이터 https://www.data.go.kr/data/15012894/standard.do (주소 이름은 확인 전이라 후보를 차례로 시도)
    "market": (["https://api.data.go.kr/openapi/tn_pubr_public_trdit_mrkt_api",
                "https://api.data.go.kr/openapi/tn_pubr_public_tradit_mrkt_api",
                "https://api.data.go.kr/openapi/tn_pubr_public_trdit_market_api"], ("mrktNm", "marketNm"), ("mrktType", "mrktSe")),
    # 전국마을회관및경로당표준데이터 https://www.data.go.kr/data/15114136/standard.do
    "senior": (["https://api.data.go.kr/openapi/tn_pubr_public_vill_hall_sen_cent_api"], ("fcltyNm", "facltNm", "vlhlNm", "senctrNm"), ("fcltySe", "facltSe", "fcltyType")),
}
REGION = "청주시"


def key():
    k = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if not k:
        sys.exit("DATA_GO_KR_KEY가 없습니다.")
    return k


def get(url, page, rows=1000):
    q = {"serviceKey": key(), "pageNo": page, "numOfRows": rows, "type": "json"}
    last = None
    for i in range(4):
        try:
            req = urllib.request.Request(url + "?" + urllib.parse.urlencode(q), headers={"User-Agent": "turnoko-pois"})
            with urllib.request.urlopen(req, timeout=30) as r:
                text = r.read().decode("utf-8", errors="replace")
            if text.lstrip().startswith("<"):
                raise PermissionError(text[:300])
            raw = json.loads(text)
            data = raw.get("response") or raw
            if "body" not in data and "header" not in data:
                raise PermissionError("알 수 없는 응답: " + text[:400])
            head = data.get("header", {})
            if str(head.get("resultCode", "00")) not in ("00", "0", "03"):
                raise PermissionError(str(head))
            body = data.get("body") or {}
            items = body.get("items") or []
            if isinstance(items, dict):
                items = items.get("item", [])
            return int(body.get("totalCount") or 0), items
        except PermissionError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep((3, 8, 15, 25)[i])
    raise RuntimeError(f"접속 실패 {url} {page}쪽: {last}")


def pick(it, names):
    for n in names:
        v = str(it.get(n) or "").strip()
        if v:
            return v
    for k, v in it.items():  # 이름 칸을 모를 때: '...Nm'으로 끝나는 첫 칸 (기관명·시도명 제외)
        if k.endswith("Nm") and k not in ("insttNm", "ctprvnNm", "signguNm", "institutionNm", "operInstitutionNm") and str(v or "").strip():
            return str(v).strip()
    return ""


def resolve(urls):
    errs = []
    for u in urls:
        try:
            get(u, 1, rows=1)
            return u
        except Exception as e:  # noqa: BLE001
            errs.append(f"{u.rsplit('/', 1)[-1]}: {e}")
    raise RuntimeError(" / ".join(errs))


def fetch(kind):
    urls, nm, se = SETS[kind]
    url = resolve(urls)
    out, page = [], 1
    while True:
        total, items = get(url, page)
        for it in items:
            addr = " ".join(str(it.get(k) or "") for k in ("rdnmadr", "lnmadr", "ctprvnNm", "signguNm", "insttNm"))
            if REGION not in addr:
                continue
            try:
                lat, lng = float(it.get("latitude")), float(it.get("longitude"))
            except (TypeError, ValueError):
                continue
            if not (36.45 < lat < 36.85 and 127.25 < lng < 127.75):  # 청주시 범위 밖 좌표 오류 제외
                continue
            name = pick(it, nm)
            h = name.split(" ")
            if len(h) % 2 == 0 and h[: len(h) // 2] == h[len(h) // 2:]:
                name = " ".join(h[: len(h) // 2])  # '○○도서관 ○○도서관' 같은 중복 이름
            out.append([name, round(lat, 6), round(lng, 6), pick(it, se)])
        if not items or page * 1000 >= total:
            break
        page += 1
        time.sleep(0.3)
    seen, uniq = set(), []
    for x in out:
        k = (x[0], round(x[1], 4), round(x[2], 4))
        if k not in seen:
            seen.add(k); uniq.append(x)
    return uniq


def probe():
    for kind, (urls, _, _) in SETS.items():
        try:
            url = resolve(urls)
            total, items = get(url, 1, rows=2)
            print(kind, url.rsplit("/", 1)[-1], "전국", total, "건, 항목:", ", ".join(sorted(items[0].keys())) if items else "")
            if items:
                print("   예:", {k: v for k, v in items[0].items() if k.endswith("Nm") or k.endswith("Se") or k in ("latitude", "longitude", "rdnmadr")})
        except Exception as e:  # noqa: BLE001
            print(kind, "실패:", e)


def update():
    prev = {}
    try:
        prev = json.load(open(OUT, encoding="utf-8"))
    except (OSError, ValueError):
        pass
    out = {"meta": {"got": datetime.now(KST).strftime("%Y-%m-%d"), "fields": ["name", "lat", "lng", "kind"],
                    "source": {"park": "전국도시공원정보표준데이터", "lib": "전국도서관표준데이터", "market": "전국전통시장표준데이터", "senior": "전국마을회관및경로당표준데이터"}}}
    ok = 0
    for kind in SETS:
        try:
            out[kind] = fetch(kind)
            print(kind, len(out[kind]), "곳")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(kind, "실패, 이전 자료 유지:", e)
            if prev.get(kind):
                out[kind] = prev[kind]
    if not ok:
        sys.exit("받은 자료가 없습니다.")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    {"probe": probe, "update": update}[sys.argv[1] if len(sys.argv) > 1 else "update"]()
