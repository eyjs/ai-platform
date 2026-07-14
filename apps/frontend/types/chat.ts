/** SSE 이벤트 타입 */
export type SSEEventType = 'token' | 'replace' | 'trace' | 'done';

/** 채팅 메시지 */
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  isStreaming: boolean;
  isError: boolean;
  errorMessage?: string;
  sources?: Array<{ title: string; url?: string }>;
  traceData?: Record<string, unknown>;
  /** RAG 파이프라인 트레이스 — SSE trace 이벤트를 순서대로 누적(답변별 펼쳐보기용). */
  traceEvents?: Array<Record<string, unknown>>;
  /** api 가 생성한 응답 식별자 (SSE done 이벤트로 수신). 피드백 제출 키. */
  responseId?: string;
  /** 사용자가 남긴 피드백 (UI 반영용). 아직 제출 전이면 undefined/null */
  feedback?: 1 | -1 | null;
  /**
   * 답변 시작 전 실시간 진행 상태 문구 (trace 이벤트 → 사람이 읽는 문장).
   * 예: "관련 문서를 검색하는 중...", "검색 범위를 넓혀 다시 확인하는 중..."
   * 토큰이 흐르기 시작하면 지워진다. 영속 의미 없음(스트리밍 중에만 표시).
   */
  statusText?: string;
}

/** 채팅 세션 — 사이드바 + localStorage 저장 단위 */
export interface ChatSession {
  id: string;
  title: string;
  profileId: string;
  profileName: string;
  createdAt: string;
  updatedAt: string;
  messages: ChatMessage[];
}

/** FastAPI ChatRequest (snake_case — 기존 호환) */
export interface ChatRequest {
  question: string;
  chatbot_id?: string;
  session_id?: string;
}

/** Profile 선택용 (FastAPI GET /api/profiles 응답) */
export interface ChatProfileOption {
  id: string;
  name: string;
  description: string | null;
  mode: string;
}
