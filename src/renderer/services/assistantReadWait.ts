/** A local deadline for GET display waits. This must never wrap a mutation or confirmed execution. */
export class AssistantReadWaitError extends Error {
  constructor(readonly code: 'read_timeout' | 'read_stopped') {
    super(code === 'read_timeout' ? '상태를 읽는 시간이 길어져 기다리기를 멈췄습니다.' : '상태 읽기 기다리기를 멈췄습니다.');
    this.name = 'AssistantReadWaitError';
  }
}

export function runBoundedAssistantRead<T>(
  read: (signal: AbortSignal) => Promise<T>,
  options: { timeoutMs?: number; signal?: AbortSignal } = {},
): Promise<T> {
  if (options.signal?.aborted) return Promise.reject(new AssistantReadWaitError('read_stopped'));
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs !== undefined && Number.isFinite(options.timeoutMs) && options.timeoutMs > 0
    ? options.timeoutMs : 15_000;
  return new Promise<T>((resolve, reject) => {
    let settled = false;
    const cleanup = () => {
      clearTimeout(timer);
      options.signal?.removeEventListener('abort', stopped);
    };
    const finish = (accept: boolean, result: unknown) => {
      if (settled) return;
      settled = true;
      cleanup();
      if (accept) resolve(result as T); else reject(result);
    };
    const retire = (code: 'read_timeout' | 'read_stopped') => {
      finish(false, new AssistantReadWaitError(code));
      controller.abort();
    };
    const stopped = () => retire('read_stopped');
    const timer = setTimeout(() => retire('read_timeout'), timeoutMs);
    options.signal?.addEventListener('abort', stopped, { once: true });
    try { Promise.resolve(read(controller.signal)).then(value => finish(true, value), cause => finish(false, cause)); }
    catch (cause) { finish(false, cause); }
  });
}
