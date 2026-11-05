# 동네레이더

청주시 청원구에서 새로 허가된 건축과 공사 진행 상황을 공공데이터로 모아 보여주는 웹사이트입니다.
매일 새벽 GitHub Actions가 국토교통부 건축HUB 건축인허가정보를 받아 자료를 갱신하고, GitHub Pages가 사이트를 보여줍니다.

## 파일 구성

| 파일 | 역할 |
| --- | --- |
| `index.html` | 웹사이트 화면 |
| `data.js` | 화면이 읽는 자료 (자동 생성, 직접 고치지 않음) |
| `fetch.py` | 자료 수집기 (설치할 패키지 없음) |
| `permits.json` | 모든 인허가 기록 원장 (자동 갱신) |
| `codes.json` | 청원구 법정동·리 코드 (첫 실행 때 자동으로 채워짐) |
| `.github/workflows/update.yml` | 매일 자동 실행 설정 |

## 처음 한 번 설정하기

### 1. 저장소 만들기

조직 `local-rader`에서 **New repository**를 누르고 이름을 `local-rader.github.io`로, 공개 범위를 **Public**으로 만듭니다.
이 이름을 쓰면 사이트 주소가 `https://local-rader.github.io`가 됩니다.

### 2. 파일 올리기

- **Add file → Upload files**에서 `index.html`, `data.js`, `fetch.py`, `permits.json`, `codes.json`, `README.md`를 한 번에 올리고 **Commit changes**를 누릅니다.
- 자동 실행 설정은 폴더째 올리기 어려우므로 **Add file → Create new file**을 누르고
  이름 칸에 `.github/workflows/update.yml`을 그대로 적은 뒤, `update.yml` 파일 내용을 붙여넣고 저장합니다.

### 3. 인증키 넣기

**Settings → Secrets and variables → Actions → New repository secret**

- Name: `DATA_GO_KR_KEY`
- Secret: 공공데이터포털의 일반 인증키

인증키는 여기에만 넣습니다. 파일에 적거나 다른 곳에 올리지 않습니다.

### 4. 자동 실행이 자료를 저장할 수 있게 허락하기

**Settings → Actions → General → Workflow permissions**에서 **Read and write permissions**를 고르고 저장합니다.
조직 설정에서 막혀 있으면 조직의 같은 메뉴에서 먼저 허용합니다.

### 5. 첫 수집 실행

**Actions** 탭 → 왼쪽 **데이터 갱신** → **Run workflow** → 날짜 칸에 `20230101` → **Run workflow**.

첫 실행은 청원구 코드를 찾고 2023년 이후 자료를 모두 받느라 10~30분 걸립니다.
초록색 체크가 뜨면 성공입니다. 빨간색이면 실행 기록의 첫 오류 문장을 확인합니다.

### 6. 사이트 켜기

**Settings → Pages → Build and deployment**에서 Source를 **Deploy from a branch**, Branch를 **main**과 **/(root)**로 고르고 저장합니다.
몇 분 뒤 `https://local-rader.github.io`에서 사이트가 열립니다.

이후로는 매일 한국시간 새벽 4시 17분에 자동으로 갱신됩니다.

## 운영하면서 챙길 것

- **인증키 활용기간**: 2028년 11월 5일까지. 만료 전에 공공데이터포털에서 연장하고 Secret을 바꿉니다.
- **예약 실행 자동 중지**: GitHub는 공개 저장소에 오랫동안 활동이 없으면 예약 실행을 멈출 수 있습니다. 멈추면 Actions 탭에서 다시 켭니다. 군 복무 전 무인 운영 30일 테스트 때 꼭 확인합니다.
- **실패 알림**: 자동 실행이 실패하면 GitHub가 계정 이메일로 알려줍니다.
- **해외 접속**: GitHub 실행 서버는 해외에 있습니다. 첫 실행에서 접속 자체가 막히면 다른 실행 방법으로 바꿔야 합니다.

## 손으로 실행하기 (선택)

```bash
export DATA_GO_KR_KEY=인증키
python fetch.py update --since 20230101   # 과거 자료 한꺼번에 받기
python fetch.py update                    # 최근 14일 동안 바뀐 기록만 받기
python fetch.py build                     # data.js만 다시 만들기
```

## 자료 기준

- 출처: 국토교통부 건축HUB 건축인허가정보 서비스 (공공데이터포털)
- 2023년 1월 1일 이후 건축허가일이 있는 기록만 사이트에 보여줍니다.
- 건물 이름은 병원·교회·빌딩처럼 시설 이름으로 보이는 것만 남기고, 개인 이름이 들어갈 수 있는 이름은 지웁니다.
- 사이트 자료는 참고용이며, 정확한 내용은 세움터에서 확인합니다.
