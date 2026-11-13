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

## 설정 순서 (처음 한 번, 30분 정도)

### 1. Claude API 키 만들기 (Anthropic)
1. https://console.anthropic.com 에 가입합니다.
2. Billing(결제)에서 결제 수단을 등록하고 크레딧을 조금(예: 5달러) 충전합니다.
3. Limits(사용 한도)에서 월 사용 한도를 낮게 정해 둡니다. 예상보다 많이 쓰이는 것을 막는 가장 확실한 방법입니다.
4. API Keys에서 새 키를 만들고 복사해 둡니다. 키는 채팅이나 사이트 파일에 붙여넣지 마세요.

### 2. Worker 만들기 (Cloudflare)
1. https://dash.cloudflare.com 에 가입합니다.
2. Workers & Pages → Create → Worker를 고르고 이름을 `turnoko-ai`처럼 정한 뒤 Deploy를 누릅니다.
3. Edit code를 눌러 기본 코드를 모두 지우고, 이 폴더의 `worker.js` 내용을 전부 붙여넣은 뒤 Deploy를 누릅니다.

### 3. 횟수 저장소(KV) 연결
1. Storage & Databases → KV → Create로 `turnoko-quota` 같은 이름의 저장소를 만듭니다.
2. 만든 Worker → Settings → Bindings → Add → KV namespace를 고릅니다.
3. Variable name에 `QUOTA`(대문자)를 쓰고, 방금 만든 저장소를 고른 뒤 저장합니다.

### 4. 비밀 값 넣기
Worker → Settings → Variables and Secrets → Add에서 Type을 Secret으로 두고 두 개를 넣습니다.
- `ANTHROPIC_API_KEY`: 1번에서 만든 키
- `SALT`: 아무 긴 문자열 (예: 비밀번호 생성기로 만든 30자)

넣은 뒤 Deploy를 한 번 더 누릅니다.

### 5. 사이트에 주소 알려주기
Worker 화면 위쪽의 주소(`https://turnoko-ai.○○○.workers.dev`)를 복사해서 알려 주세요.
이 주소는 공개돼도 괜찮습니다. config.js의 `aiEndpoint`에 넣으면 질문창이 열립니다.

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
