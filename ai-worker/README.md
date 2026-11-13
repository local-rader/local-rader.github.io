# 터놓고 AI 질문 중계 서버 설정

사이트의 'AI 질문' 화면에서 직접 쓴 질문을 Claude에게 전달하는 작은 서버입니다.
Cloudflare Workers(무료)에서 돌아가고, Claude API 키는 이 서버에만 넣습니다.
사이트(config.js)에는 키를 넣지 않습니다. 넣으면 누구나 볼 수 있습니다.

질문 버튼은 이 서버 없이도 동작합니다. 이 설정을 마치기 전에는 직접 쓰는 질문창만 '준비 중'으로 나옵니다.

## 동작 방식

- 터놓고 사이트(https://local-rader.github.io)에서 온 질문만 받습니다.
- 무료: 기기마다 한 주에 2번 (월요일 0시, 한국 시간에 다시 2번). 기기 번호가 없으면 IP로 셉니다.
- 유료(하루 5번)는 구조만 있고 아직 아무에게도 주지 않습니다. 결제를 붙일 때 `planOf()`에서 구독을 확인하면 됩니다.
- 안전장치: 같은 IP는 한 주 최대 10번, 사이트 전체는 하루 최대 200번 (설정으로 바꿀 수 있음).
- 질문에 나온 동네(최대 3곳)와 청주시 전체 요약만 골라 Claude에게 보냅니다. 자료는 사이트의 `ai-context.json`(매일 자동 갱신)입니다.
- 답을 받았을 때만 횟수를 셉니다. AI가 실패하면 횟수가 줄지 않습니다.
- 질문과 답은 저장하지 않습니다. 횟수 세기용으로 기기·IP를 해시한 값과 횟수만 최대 8일 둡니다.

## 설정 순서 (처음 한 번)

### 방법 A: GitHub가 대신 배포 (추천, 20분 정도)
값 세 개만 GitHub Secrets에 넣으면, `AI 서버 배포` 자동 작업(.github/workflows/ai-worker.yml)이
Worker 만들기, 횟수 저장소(KV) 연결, 키 넣기, 시험까지 합니다. 이후 `ai-worker/`가 바뀌면 자동으로 다시 배포합니다.

1. **Claude API 키** (https://console.anthropic.com)
   - 가입 → Billing에서 결제 수단 등록, 크레딧 조금(예: 5달러) 충전
   - Limits에서 월 사용 한도를 낮게(예: 5달러) 정하기
   - API Keys → Create Key → 키 복사 (채팅에 붙여넣지 않기)
2. **Cloudflare API 토큰과 계정 ID** (https://dash.cloudflare.com, 무료 가입)
   - 오른쪽 위 사람 아이콘 → My Profile → API Tokens → Create Token
   - 'Edit Cloudflare Workers' 템플릿의 Use template → 아래로 내려 Continue to summary → Create Token → 토큰 복사
   - 대시보드 첫 화면(또는 Workers & Pages 화면) 오른쪽의 Account ID 복사
3. **GitHub에 넣기**: 저장소 → Settings → Secrets and variables → Actions → New repository secret으로 세 개
   - `ANTHROPIC_API_KEY` = 1번 키
   - `CLOUDFLARE_API_TOKEN` = 2번 토큰
   - `CLOUDFLARE_ACCOUNT_ID` = 2번 계정 ID
4. 다 넣었다고 알려 주면 `AI 서버 배포`를 실행하고, 나온 주소를 config.js의 `aiEndpoint`에 넣습니다.

### 방법 B: Cloudflare 화면에서 직접
1. Claude API 키는 방법 A의 1번과 같습니다.
2. Workers & Pages → Create → Worker, 이름 `turnoko-ai` → Deploy → Edit code에 `worker.js` 전체 붙여넣기 → Deploy
3. Storage & Databases → KV → Create로 `turnoko-quota` 만들기 → Worker → Settings → Bindings → Add → KV namespace, 이름 `QUOTA`
4. Settings → Variables and Secrets → Secret으로 `ANTHROPIC_API_KEY`(키)와 `SALT`(아무 긴 문자열) 넣고 Deploy
5. Worker 주소(`https://turnoko-ai.○○○.workers.dev`)를 config.js의 `aiEndpoint`에 넣기

## 바꿀 수 있는 설정 (Variables, 선택)

| 이름 | 기본값 | 뜻 |
|---|---|---|
| `DAILY_CAP` | 200 | 사이트 전체 하루 최대 AI 질문 수 |
| `IP_WEEKLY_CAP` | 10 | 같은 IP의 한 주 최대 질문 수 |
| `MODEL` | claude-haiku-5-5 | 쓸 Claude 모델 |
| `ALLOWED_ORIGINS` | https://local-rader.github.io | 질문을 받을 사이트 주소 |
| `CONTEXT_URL` | https://local-rader.github.io/ai-context.json | 동네 요약 자료 주소 |

## 비용 감각

질문 하나에 자료 약 3,500자와 답 최대 700토큰을 씁니다. 작은 모델(claude-haiku-5-5) 기준으로 질문 하나에 1원 안팎으로 예상합니다.
하루 상한 200번이면 하루 최대 수백 원 수준입니다. 정확한 단가는 Anthropic 가격표에서 확인하세요.
