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
