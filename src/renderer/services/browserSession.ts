import type { SharedConnection } from '../../types/electron';

/** A same-origin browser client uses the server's HttpOnly cookie. Only the
 * CSRF proof and public connection metadata live in this tab's memory.
 * Reload requires login again; no password or session token is persisted. */
export class BrowserSession {
  private current: (SharedConnection & { csrf: string }) | null = null;
  private generation = 0;
  constructor(private readonly runtime: {
    origin: () => string | null;
    fetch: typeof fetch;
    now: () => number;
  }) {}

  available(): boolean {
    const origin = this.runtime.origin();
    if (!origin) return false;
    try { return new URL(origin).protocol === 'https:'; } catch { return false; }
  }

  connection(): SharedConnection | null {
    if (!this.current || this.current.expires_at <= this.runtime.now()) {
      this.current = null;
      return null;
    }
    const { csrf: _csrf, ...publicValue } = this.current;
    return { ...publicValue, user: { ...publicValue.user } };
  }

  requestOptions(target: string, method = 'GET'): { credentials?: RequestCredentials; headers?: Record<string, string> } {
    if (!this.connection()) return {};
    const url = new URL(target);
    if (url.origin !== this.current!.server_url || !/^\/api\//.test(url.pathname)) return {};
    return { credentials: 'same-origin', headers: /^(GET|HEAD|OPTIONS)$/i.test(method)
      ? {} : { 'X-Vision-CSRF': this.current!.csrf } };
  }

  async login(input: { server_url: string; username: string; password: string }): Promise<SharedConnection> {
    const url = new URL(input.server_url);
    if (!this.available() || url.origin !== this.runtime.origin() || url.protocol !== 'https:'
      || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
      throw new Error('브라우저는 현재 페이지와 같은 HTTPS 서버에만 로그인할 수 있습니다. 해당 서버의 Studio 페이지를 열어주세요.');
    }
    if (!/^[-A-Za-z0-9_.@]{3,80}$/.test(input.username) || input.password.length < 12 || input.password.length > 1024)
      throw new Error('계정 이름과 12자 이상의 비밀번호를 확인하세요.');
    const epoch = ++this.generation;
    this.current = null;
    const response = await this.runtime.fetch(url.origin + '/api/accounts/login', {
      method: 'POST', credentials: 'same-origin', redirect: 'error', signal: AbortSignal.timeout(15000),
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: input.username, password: input.password, transport: 'cookie' }),
    });
    const value = await response.json();
    if (epoch !== this.generation) throw new Error('연결 상태가 바뀌었습니다. 다시 로그인하세요.');
    if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : '공유 서버 로그인에 실패했습니다.');
    if ('token' in value || !Number.isFinite(value.expires_at) || value.expires_at <= this.runtime.now()
      || typeof value.csrf_token !== 'string' || !/^[A-Za-z0-9_-]{20,256}$/.test(value.csrf_token)
      || typeof value.user?.id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value.user.id)
      || typeof value.user.username !== 'string' || typeof value.user.administrator !== 'boolean' && ![0, 1].includes(value.user.administrator))
      throw new Error('서버가 유효한 브라우저 세션을 반환하지 않았습니다.');
    this.current = { server_url: url.origin, expires_at: value.expires_at,
      user: { id: value.user.id, username: value.user.username, administrator: value.user.administrator }, csrf: value.csrf_token };
    return this.connection()!;
  }

  select(projectId: string): SharedConnection {
    if (!this.connection() || !/^[A-Za-z0-9_-]{1,128}$/.test(projectId))
      throw new Error('유효한 브라우저 세션과 공유 프로젝트 선택을 확인하세요.');
    this.current!.project_id = projectId;
    return this.connection()!;
  }

  async disconnect(): Promise<void> {
    const previous = this.current;
    this.generation++;
    this.current = null;
    if (!previous) return;
    const response = await this.runtime.fetch(previous.server_url + '/api/accounts/logout', {
      method: 'POST', credentials: 'same-origin', redirect: 'error', signal: AbortSignal.timeout(5000),
      headers: { 'X-Vision-CSRF': previous.csrf },
    });
    if (!response.ok) throw new Error('이 탭의 연결은 해제했지만 서버 세션 종료를 확인하지 못했습니다. 다시 로그인하여 종료하세요.');
  }
}

export const browserSession = new BrowserSession({
  origin: () => typeof window !== 'undefined' && !window.api ? window.location?.origin ?? null : null,
  fetch: (...args) => fetch(...args), now: () => Date.now() / 1000,
});
