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

# 공공데이터포털 검색어 (승인 목록 제목 그대로)
QUERIES = [
    "119안전센터 현황",
    "119안전센터 파출장소 치안센터 주소",
    "청주도시공사 운영 시설 위치 정보",
    "청주시시설관리공단 운영 시설",
    "충청북도 공공체육시설 현황",
    "충청북도 청주시 의료시설",
    "요양기관 개설 현황",
    "병원코드정보서비스",
    "전국주차장정보표준데이터",
    "충청북도 어린이복지시설",
]


def key():
    k = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if not k:
        sys.exit("DATA_GO_KR_KEY가 없습니다.")
    return k


def fetch(url, tries=3):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
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


def search(q):
    url = "https://www.data.go.kr/tcs/dss/selectDataSetList.do?" + urllib.parse.urlencode(
        {"dType": "", "keyword": q, "currentPage": 1, "perPage": 10, "sort": "_score"})
    html = fetch(url)
    out, seen = [], set()
    for m in re.finditer(r'href="/data/(\d+)/(\w+)\.do[^"]*"', html):
        pid, kind = m.group(1), m.group(2)
        if (pid, kind) in seen:
            continue
        seen.add((pid, kind))
        tail = re.sub(r"<[^>]+>", " ", html[m.end():m.end() + 600])
        title = re.sub(r"\s+", " ", tail).strip()[:90]
        out.append((pid, kind, title))
    return out[:6]


def probe_file(pid):
    doc = fetch(f"https://infuser.odcloud.kr/oas/docs?namespace={pid}/v1")
    try:
        paths = list(json.loads(doc).get("paths", {}).items())
    except ValueError:
        print("    swagger 없음:", doc[:150].replace("\n", " "))
        return
    for p, spec in paths[-3:]:  # 최신 버전이 보통 마지막
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


def probe_api(pid, kind):
    page = fetch(f"https://www.data.go.kr/data/{pid}/{kind}.do")
    urls = sorted(set(re.findall(r"https?://apis?\.data\.go\.kr/[A-Za-z0-9_/\.\-]+", page)))
    print("    API 주소 후보:", urls[:8])
    for u in urls[:6]:
        q = {"serviceKey": key(), "pageNo": 1, "numOfRows": 2, "type": "json", "_type": "json"}
        txt = fetch(u + "?" + urllib.parse.urlencode(q), tries=1)
        print(f"    → {u} : {hide(txt[:500]).replace(chr(10), ' ')}")


def probe():
    done = set()
    for q in QUERIES:
        print(f"\n## {q}")
        for pid, kind, title in search(q):
            print(f"  - {pid} {kind}: {title}")
            if pid in done:
                continue
            done.add(pid)
            if kind == "fileData":
                probe_file(pid)
            else:
                probe_api(pid, kind)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"
    if cmd == "probe":
        probe()
    else:
        sys.exit("update는 probe 결과를 보고 채웁니다.")
