"""생활권 점수 추가 자료 (2026-11 승인분) → newdata.json

  python newdata.py probe    # 자료별 API 주소 찾기 + 응답 항목 이름 확인 (로그만)
  python newdata.py update   # 청주 행만 받아 newdata.json 저장
인증키: DATA_GO_KR_KEY (공공데이터포털에서 활용신청 승인된 계정)
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 turnoko-newdata"}

# 승인된 자료 (공공데이터포털 자료 번호). 파일데이터는 odcloud API로 받는다.
FILES = {
    "safety119": 15065056,     # 소방청_119안전센터 현황
    "police": 15105156,        # 해양경찰청 파출장소·경찰청 치안센터·소방청 119안전센터 주소
    "cityfac": 15143611,       # 청주도시공사_운영 시설 위치 정보
    "sports": 15083343,        # 충청북도_공공체육시설 현황
    "medical": 15110848,       # 충청북도 청주시_의료시설
    "care": 15051057,          # 건강보험심사평가원_요양기관 개설 현황
    "child": 15090401,         # 충청북도_어린이복지시설
}
STANDARD = {
    "parking": ["https://api.data.go.kr/openapi/tn_pubr_prkplce_info_api"],  # 전국주차장정보표준데이터
}
def key():
    k = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if not k:
        sys.exit("DATA_GO_KR_KEY가 없습니다.")
    return k


def fetch(url, tries=2):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
                raw = r.read()
            for enc in ("utf-8", "cp949"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    pass
            return raw.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 + 5 * i)
    return f"__ERR__ {last}"


def hide(s):
    return s.replace(key(), "KEY").replace(urllib.parse.quote(key(), safe=""), "KEY")


def probe_file(pid):
    doc = fetch(f"https://infuser.odcloud.kr/oas/docs?namespace={pid}/v1")
    try:
        paths = list(json.loads(doc).get("paths", {}).items())
    except ValueError:
        print("    swagger 없음:", doc[:150].replace("\n", " "))
        return
    for p, spec in paths[-2:]:  # 최신 버전이 보통 마지막
        summ = (spec.get("get") or {}).get("summary", "")
        url = f"https://api.odcloud.kr/api{p}?" + urllib.parse.urlencode({"page": 1, "perPage": 2, "serviceKey": key()})
        txt = fetch(url)
        try:
            j = json.loads(txt)
            d = j.get("data") or []
            print(f"    [file] {p} | {summ} | total={j.get('totalCount')} | 칸={list(d[0].keys()) if d else j}")
            if d:
                print("      예:", json.dumps(d[0], ensure_ascii=False)[:400])
        except ValueError:
            print(f"    [file] {p} | {summ} | 응답: {hide(txt[:200])}")


def probe():
    for name, pid in FILES.items():
        print(f"\n## {name} ({pid})")
        probe_file(pid)
    for name, urls in STANDARD.items():
        print(f"\n## {name}")
        for u in urls:
            q = {"serviceKey": key(), "pageNo": 1, "numOfRows": 2, "type": "json"}
            print("   ", u, hide(fetch(u + "?" + urllib.parse.urlencode(q), tries=1)[:900]).replace(chr(10), " "))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"
    if cmd == "probe":
        probe()
    else:
        sys.exit("update는 probe 결과를 보고 채웁니다.")
