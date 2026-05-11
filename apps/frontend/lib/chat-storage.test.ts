import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import type { ChatSession, ChatMessage } from '@/types/chat';
import {
  loadSessions,
  saveSessions,
  createNewSession,
  addMessageToSession,
  updateLastMessage,
  groupSessionsByDate,
} from './chat-storage';

const STORAGE_KEY = 'aip-chat-sessions';
const MAX_SESSIONS = 100;

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 'msg-1',
    role: 'user',
    content: '안녕하세요',
    timestamp: new Date().toISOString(),
    isStreaming: false,
    isError: false,
    ...overrides,
  };
}

function makeSession(overrides: Partial<ChatSession> = {}): ChatSession {
  const now = new Date().toISOString();
  return {
    id: 'session-1',
    title: '새 대화',
    profileId: 'profile-1',
    profileName: '테스트 프로필',
    createdAt: now,
    updatedAt: now,
    messages: [],
    ...overrides,
  };
}

describe('loadSessions()', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('localStorage가 비어있으면 빈 배열을 반환한다', () => {
    expect(loadSessions()).toEqual([]);
  });

  it('유효한 JSON이 있으면 파싱하여 반환한다', () => {
    const sessions: ChatSession[] = [makeSession()];
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
    expect(loadSessions()).toEqual(sessions);
  });

  it('잘못된 JSON이 있으면 빈 배열을 반환한다', () => {
    localStorage.setItem(STORAGE_KEY, '{invalid json}');
    expect(loadSessions()).toEqual([]);
  });

  it('여러 세션을 모두 반환한다', () => {
    const sessions: ChatSession[] = [
      makeSession({ id: 'session-1' }),
      makeSession({ id: 'session-2' }),
    ];
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
    expect(loadSessions()).toHaveLength(2);
  });
});

describe('saveSessions()', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('세션을 localStorage에 저장한다', () => {
    const sessions: ChatSession[] = [makeSession()];
    saveSessions(sessions);
    const stored = localStorage.getItem(STORAGE_KEY);
    expect(stored).not.toBeNull();
    expect(JSON.parse(stored!)).toEqual(sessions);
  });

  it('localStorage.setItem을 호출한다', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem');
    const sessions: ChatSession[] = [makeSession()];
    saveSessions(sessions);
    expect(spy).toHaveBeenCalledWith(STORAGE_KEY, JSON.stringify(sessions));
    spy.mockRestore();
  });

  it(`MAX_SESSIONS(${MAX_SESSIONS})개 초과 시 앞에서 자른다`, () => {
    const sessions: ChatSession[] = Array.from({ length: MAX_SESSIONS + 10 }, (_, i) =>
      makeSession({ id: `session-${i}` }),
    );
    saveSessions(sessions);
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY)!);
    expect(stored).toHaveLength(MAX_SESSIONS);
    // 앞쪽 MAX_SESSIONS개만 저장되어야 한다
    expect(stored[0].id).toBe('session-0');
    expect(stored[MAX_SESSIONS - 1].id).toBe(`session-${MAX_SESSIONS - 1}`);
  });

  it('정확히 MAX_SESSIONS개이면 그대로 저장한다', () => {
    const sessions: ChatSession[] = Array.from({ length: MAX_SESSIONS }, (_, i) =>
      makeSession({ id: `session-${i}` }),
    );
    saveSessions(sessions);
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY)!);
    expect(stored).toHaveLength(MAX_SESSIONS);
  });
});

describe('createNewSession()', () => {
  it('올바른 구조의 세션을 생성한다', () => {
    const session = createNewSession('profile-1', '테스트 프로필');
    expect(session).toMatchObject({
      title: '새 대화',
      profileId: 'profile-1',
      profileName: '테스트 프로필',
      messages: [],
    });
    expect(session.id).toBeTruthy();
    expect(session.createdAt).toBeTruthy();
    expect(session.updatedAt).toBeTruthy();
  });

  it('고유한 id를 생성한다', () => {
    const s1 = createNewSession('profile-1', '프로필1');
    const s2 = createNewSession('profile-1', '프로필1');
    expect(s1.id).not.toBe(s2.id);
  });

  it('createdAt과 updatedAt이 ISO 형식 문자열이다', () => {
    const session = createNewSession('profile-1', '프로필1');
    expect(() => new Date(session.createdAt)).not.toThrow();
    expect(() => new Date(session.updatedAt)).not.toThrow();
    expect(new Date(session.createdAt).toISOString()).toBe(session.createdAt);
    expect(new Date(session.updatedAt).toISOString()).toBe(session.updatedAt);
  });

  it('messages가 빈 배열이다', () => {
    const session = createNewSession('profile-1', '프로필1');
    expect(session.messages).toEqual([]);
  });
});

describe('addMessageToSession()', () => {
  it('메시지를 세션에 추가한다', () => {
    const session = makeSession();
    const message = makeMessage({ content: '안녕하세요' });
    const updated = addMessageToSession(session, message);
    expect(updated.messages).toHaveLength(1);
    expect(updated.messages[0]).toEqual(message);
  });

  it('기존 메시지를 변경하지 않는다 (불변성)', () => {
    const session = makeSession();
    const message = makeMessage();
    const updated = addMessageToSession(session, message);
    expect(session.messages).toHaveLength(0);
    expect(updated).not.toBe(session);
  });

  it('제목이 "새 대화"이고 첫 사용자 메시지이면 내용으로 제목을 설정한다', () => {
    const session = makeSession({ title: '새 대화' });
    const message = makeMessage({ role: 'user', content: '안녕하세요 반갑습니다' });
    const updated = addMessageToSession(session, message);
    expect(updated.title).toBe('안녕하세요 반갑습니다');
  });

  it('50자 초과 메시지는 50자로 잘린다', () => {
    const session = makeSession({ title: '새 대화' });
    const longContent = 'a'.repeat(60);
    const message = makeMessage({ role: 'user', content: longContent });
    const updated = addMessageToSession(session, message);
    expect(updated.title).toBe('a'.repeat(50));
  });

  it('제목이 이미 설정된 경우 제목을 변경하지 않는다', () => {
    const session = makeSession({ title: '기존 제목' });
    const message = makeMessage({ role: 'user', content: '새 메시지' });
    const updated = addMessageToSession(session, message);
    expect(updated.title).toBe('기존 제목');
  });

  it('어시스턴트 메시지는 제목을 변경하지 않는다', () => {
    const session = makeSession({ title: '새 대화' });
    const message = makeMessage({ role: 'assistant', content: '안녕하세요' });
    const updated = addMessageToSession(session, message);
    expect(updated.title).toBe('새 대화');
  });

  it('updatedAt이 갱신된다', () => {
    const session = makeSession({ updatedAt: '2020-01-01T00:00:00.000Z' });
    const message = makeMessage();
    const updated = addMessageToSession(session, message);
    expect(updated.updatedAt).not.toBe('2020-01-01T00:00:00.000Z');
  });
});

describe('updateLastMessage()', () => {
  it('마지막 메시지를 업데이트한다', () => {
    const messages = [
      makeMessage({ id: 'msg-1', content: '첫 번째' }),
      makeMessage({ id: 'msg-2', content: '두 번째' }),
    ];
    const session = makeSession({ messages });
    const updated = updateLastMessage(session, (msg) => ({ ...msg, content: '업데이트됨' }));
    expect(updated.messages[1].content).toBe('업데이트됨');
    expect(updated.messages[0].content).toBe('첫 번째');
  });

  it('메시지가 없으면 세션을 그대로 반환한다', () => {
    const session = makeSession({ messages: [] });
    const updated = updateLastMessage(session, (msg) => ({ ...msg, content: '업데이트됨' }));
    expect(updated).toBe(session);
  });

  it('기존 세션을 변경하지 않는다 (불변성)', () => {
    const messages = [makeMessage({ id: 'msg-1', content: '원본' })];
    const session = makeSession({ messages });
    const updated = updateLastMessage(session, (msg) => ({ ...msg, content: '업데이트됨' }));
    expect(session.messages[0].content).toBe('원본');
    expect(updated).not.toBe(session);
  });

  it('updatedAt이 갱신된다', () => {
    const messages = [makeMessage()];
    const session = makeSession({ messages, updatedAt: '2020-01-01T00:00:00.000Z' });
    const updated = updateLastMessage(session, (msg) => msg);
    expect(updated.updatedAt).not.toBe('2020-01-01T00:00:00.000Z');
  });
});

describe('groupSessionsByDate()', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  function makeSessionAt(dateIso: string, id: string): ChatSession {
    return makeSession({ id, updatedAt: dateIso, createdAt: dateIso });
  }

  it('5개의 그룹 키를 반드시 포함한다', () => {
    const groups = groupSessionsByDate([]);
    expect([...groups.keys()]).toEqual(['오늘', '어제', '이전 7일', '이전 30일', '그 이전']);
  });

  it('오늘 날짜 세션을 "오늘" 그룹에 넣는다', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2024-06-15T12:00:00.000Z'));

    const todaySession = makeSessionAt('2024-06-15T09:00:00.000Z', 'today');
    const groups = groupSessionsByDate([todaySession]);
    expect(groups.get('오늘')).toHaveLength(1);
    expect(groups.get('오늘')![0].id).toBe('today');
  });

  it('어제 날짜 세션을 "어제" 그룹에 넣는다', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2024-06-15T12:00:00.000Z'));

    const yesterdaySession = makeSessionAt('2024-06-14T09:00:00.000Z', 'yesterday');
    const groups = groupSessionsByDate([yesterdaySession]);
    expect(groups.get('어제')).toHaveLength(1);
    expect(groups.get('어제')![0].id).toBe('yesterday');
  });

  it('2-7일 전 세션을 "이전 7일" 그룹에 넣는다', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2024-06-15T12:00:00.000Z'));

    const session = makeSessionAt('2024-06-10T09:00:00.000Z', 'week');
    const groups = groupSessionsByDate([session]);
    expect(groups.get('이전 7일')).toHaveLength(1);
    expect(groups.get('이전 7일')![0].id).toBe('week');
  });

  it('8-30일 전 세션을 "이전 30일" 그룹에 넣는다', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2024-06-15T12:00:00.000Z'));

    const session = makeSessionAt('2024-05-25T09:00:00.000Z', 'month');
    const groups = groupSessionsByDate([session]);
    expect(groups.get('이전 30일')).toHaveLength(1);
    expect(groups.get('이전 30일')![0].id).toBe('month');
  });

  it('30일 초과 세션을 "그 이전" 그룹에 넣는다', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2024-06-15T12:00:00.000Z'));

    const session = makeSessionAt('2024-01-01T09:00:00.000Z', 'old');
    const groups = groupSessionsByDate([session]);
    expect(groups.get('그 이전')).toHaveLength(1);
    expect(groups.get('그 이전')![0].id).toBe('old');
  });

  it('세션을 updatedAt 내림차순으로 정렬한다', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2024-06-15T12:00:00.000Z'));

    const older = makeSessionAt('2024-06-15T08:00:00.000Z', 'older');
    const newer = makeSessionAt('2024-06-15T10:00:00.000Z', 'newer');
    const groups = groupSessionsByDate([older, newer]);
    const todayGroup = groups.get('오늘')!;
    expect(todayGroup[0].id).toBe('newer');
    expect(todayGroup[1].id).toBe('older');
  });

  it('빈 배열을 전달하면 모든 그룹이 비어있다', () => {
    const groups = groupSessionsByDate([]);
    for (const sessions of groups.values()) {
      expect(sessions).toHaveLength(0);
    }
  });
});
