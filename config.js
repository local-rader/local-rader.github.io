// 사이트 설정. 카카오 지도 JavaScript 키는 등록한 도메인(local-rader.github.io)에서만 동작하므로 공개돼도 됩니다.
// 키가 비어 있으면 지도 탭에 안내 문구만 나옵니다.
window.DONGNE_CONFIG = {
  kakaoJsKey: "aa96866b347b104d71bcb5ece9f0070b",
  // AI 질문 중계 서버(Cloudflare Worker) 주소. 비어 있으면 AI 질문창은 '준비 중'으로 나오고 질문 버튼만 동작합니다.
  // Claude API 키는 여기에 넣지 않습니다(누구나 볼 수 있음). 키는 Worker 설정에만 넣습니다.
  aiEndpoint: ""
};
