"""생활권 점수 추가 시설 (상가정보에서) → extra.json

  python make_extra.py 소상공인시장진흥공단_상가_상권_정보_충북_YYYYMM.csv

  academy   학원: 입시·교과학원 + 외국어학원 (학업 학원만. 예체능·성인 직업훈련 제외)
  bigstore  대형마트·백화점: 이마트·홈플러스·롯데마트·코스트코·트레이더스 대형점, 백화점, 대형 아울렛
            상가정보에는 대형점 분류가 따로 없고 매장 안 가게들이 '○○홈플러스 오창점'처럼 잡혀서,
            점포마다 입점 가게 좌표의 중앙값을 점포 위치로 쓴다. (익스프레스·에브리데이·이마트24 같은 소형점 제외)
"""
import csv
import io
import json
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CITYK = os.environ.get("TURNOKO_CITY", "청주").strip() or "청주"   # 충주면 chungju/extra.json
CITY_DIR = {"청주": "", "충주": "chungju"}[CITYK]
ACADEMY = {"입시·교과학원", "외국어학원"}
# (점포 이름, 상호명 정규식, 법정동)
STORES = [
    ("현대백화점 충청점", r"현대백화점", None),
    ("롯데아울렛 청주점", r"롯데청주아울렛|롯데아울렛|청주롯데아울렛", None),
    ("이마트 청주점", r"이마트\s?청주|청주이마트", "미평동"),
    ("이마트 청주사천점", r"이마트\s?청주사천", None),
    ("홈플러스 청주점", r"홈플러스", "가경동"),
    ("홈플러스 동청주점", r"홈플러스", "율량동"),
    ("홈플러스 오창점", r"홈플러스", "오창읍"),
    ("롯데마트", r"롯데마트", None),
    ("코스트코", r"코스트코", None),
    ("트레이더스", r"트레이더스\s?홀세일|이마트\s?트레이더스", None),
]
STORES_BY_CITY = {"청주": STORES, "충주": [
    ("이마트 충주점", r"이마트", "문화동"),
    ("롯데마트 충주점", r"롯데마트", "칠금동"),
]}
SMALL = re.compile(r"이마트24|익스프레스|에브리데이|노브랜드")


def main(path):
    raw = open(path, "rb").read()
    try:
        txt = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        txt = raw.decode("cp949")
    rows = [r for r in csv.DictReader(io.StringIO(txt)) if CITYK in r.get("시군구명", "")]
    out = {"meta": {"source": os.path.basename(path), "fields": ["name", "lat", "lng", "kind"],
                    "note": "학원=입시·교과+외국어학원. 대형점=입점 가게 좌표 중앙값"}, "academy": [], "bigstore": []}
    for r in rows:
        if r["상권업종소분류명"] in ACADEMY:
            try:
                out["academy"].append([r["상호명"], round(float(r["위도"]), 6), round(float(r["경도"]), 6), r["상권업종소분류명"]])
            except ValueError:
                pass
    for name, pat, dong in STORES_BY_CITY[CITYK]:
        hit = [r for r in rows if re.search(pat, r["상호명"] + " " + r.get("지점명", "")) and not SMALL.search(r["상호명"]) and (dong is None or r["법정동명"] == dong)]
        if not hit:
            print(f"  {name}: 상가정보에 없음")
            continue
        lat = statistics.median(float(r["위도"]) for r in hit)
        lng = statistics.median(float(r["경도"]) for r in hit)
        out["bigstore"].append([name, round(lat, 6), round(lng, 6), f"입점 {len(hit)}곳"])
        print(f"  {name}: 입점 {len(hit)}곳 → {lat:.5f}, {lng:.5f} ({hit[0]['법정동명']})")
    with open(os.path.join(HERE, CITY_DIR, "extra.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("extra.json:", {k: len(v) for k, v in out.items() if k != "meta"})


if __name__ == "__main__":
    main(sys.argv[1])
