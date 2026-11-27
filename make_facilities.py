"""생활권 점수용 시설 좌표 → facilities.json (청주 범위)

입력 (직접 내려받은 표준데이터 파일)
  · 전국초중등학교위치표준데이터 CSV  → school (운영 중인 초·중·고, 소재지가 청주)
  · 소상공인시장진흥공단 상가(상권)정보 CSV (충북) → hospital·pharmacy·mart (청주 업소)
    - hospital: 병원 대분류 전체 + 의원 대분류 중 치과·한의원 제외
    - pharmacy: 의약·화장품 소매 › 약국
    - mart: 종합 소매 › 슈퍼마켓·편의점

사용법: python make_facilities.py 학교.csv 상가.csv
"""
import csv, json, sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
CITY = os.environ.get("TURNOKO_CITY", "청주").strip() or "청주"   # 충주면 chungju/facilities.json
CITY_DIR = {"청주": "", "충주": "chungju"}[CITY]


def read_csv(path):
    for enc in ("utf-8-sig", "cp949"):
        try:
            with open(path, encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    sys.exit(f"인코딩을 읽을 수 없음: {path}")


def school_points(path):
    out = []
    for r in read_csv(path):
        if r.get("운영상태") != "운영":
            continue
        if r.get("학교급구분") not in ("초등학교", "중학교", "고등학교"):
            continue
        addr = (r.get("소재지지번주소") or "") + " " + (r.get("소재지도로명주소") or "")
        if CITY + "시" not in addr:
            continue
        try:
            lat, lng = float(r["위도"]), float(r["경도"])
        except (KeyError, ValueError):
            continue
        out.append([r["학교명"], lat, lng])
    return out


def store_points(path):
    pts = {"hospital": [], "pharmacy": [], "mart": []}
    for r in read_csv(path):
        if r.get("시도명") != "충청북도" or not str(r.get("시군구명", "")).startswith(CITY):
            continue
        try:
            lat, lng = float(r["위도"]), float(r["경도"])
        except (KeyError, ValueError):
            continue
        big, mid, small = r.get("상권업종대분류명"), r.get("상권업종중분류명"), r.get("상권업종소분류명")
        name = r.get("상호명", "")
        if big == "보건의료" and mid in ("병원", "의원"):
            if mid == "의원" and small in ("치과의원", "한의원"):
                continue
            pts["hospital"].append([name, lat, lng])
        elif mid == "약국" or small == "약국":
            pts["pharmacy"].append([name, lat, lng])
        elif small in ("슈퍼마켓", "편의점"):
            pts["mart"].append([name, lat, lng])
    return pts


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    school = school_points(sys.argv[1])
    stores = store_points(sys.argv[2])
    out = {
        "meta": {
            "name": f"생활권 시설 좌표 ({CITY})",
            "sources": {
                "school": "전국초중등학교위치표준데이터 (한국교육시설안전원, 수정일 2026-10-06 기준 파일)",
                "hospital": "소상공인시장진흥공단 상가(상권)정보 202609 (보건의료 › 병원·의원, 치과·한의원 제외)",
                "pharmacy": "소상공인시장진흥공단 상가(상권)정보 202609 (의약·화장품 소매 › 약국)",
                "mart": "소상공인시장진흥공단 상가(상권)정보 202609 (종합 소매 › 슈퍼마켓·편의점)",
            },
        },
        "school": school, **stores,
    }
    with open(os.path.join(HERE, CITY_DIR, "facilities.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print({k: len(v) for k, v in out.items() if k != "meta"})


if __name__ == "__main__":
    main()
