import type { ChatSession, ChatMessage } from '@/types/chat';

const STORAGE_KEY = 'aip-chat-sessions';
const MAX_SESSIONS = 100;

/** localStorage에서 세션 목록 로드 */
export function loadSessions(): ChatSession[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as ChatSession[];
  } catch {
    return [];
  }
}

/** 세션 목록을 localStorage에 저장 */
export function saveSessions(sessions: ChatSession[]): void {
  if (typeof window === 'undefined') return;
  // FIFO: 최대 100개 유지
  const trimmed = sessions.slice(0, MAX_SESSIONS);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
}

/** 새 세션 생성 */
export function createNewSession(
  profileId: string,
  profileName: string,
): ChatSession {
  const now = new Date().toISOString();
  return {
    id: crypto.randomUUID(),
    title: '새 대화',
    profileId,
    profileName,
    createdAt: now,
    updatedAt: now,
    messages: [],
  };
}

/** 세션에 메시지 추가 */
export function addMessageToSession(
  session: ChatSession,
  message: ChatMessage,
): ChatSession {
  const updatedMessages = [...session.messages, message];
  const title =
    session.title === '새 대화' && message.role === 'user'
      ? message.content.slice(0, 50)
      : session.title;
  return {
    ...session,
    title,
    messages: updatedMessages,
    updatedAt: new Date().toISOString(),
  };
}

/** 세션의 마지막 메시지 업데이트 */
export function updateLastMessage(
  session: ChatSession,
  updater: (msg: ChatMessage) => ChatMessage,
): ChatSession {
  if (session.messages.length === 0) return session;
  const messages = [...session.messages];
  messages[messages.length - 1] = updater(messages[messages.length - 1]);
  return { ...session, messages, updatedAt: new Date().toISOString() };
}

/** 날짜 그룹 분류 */
export function groupSessionsByDate(
  sessions: ChatSession[],
): Map<string, ChatSession[]> {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);
  const last7Days = new Date(today.getTime() - 7 * 86400000);
  const last30Days = new Date(today.getTime() - 30 * 86400000);

  const groups = new Map<string, ChatSession[]>();
  groups.set('오늘', []);
  groups.set('어제', []);
  groups.set('이전 7일', []);
  groups.set('이전 30일', []);
  groups.set('그 이전', []);

  const sorted = [...sessions].sort(
    (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
  );

  for (const session of sorted) {
    const date = new Date(session.updatedAt);
    if (date >= today) {
      groups.get('오늘')!.push(session);
    } else if (date >= yesterday) {
      groups.get('어제')!.push(session);
    } else if (date >= last7Days) {
      groups.get('이전 7일')!.push(session);
    } else if (date >= last30Days) {
      groups.get('이전 30일')!.push(session);
    } else {
      groups.get('그 이전')!.push(session);
    }
  }

  return groups;
}
