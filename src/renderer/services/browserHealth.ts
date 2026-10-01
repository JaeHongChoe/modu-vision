/**
 * Backend health for a renderer without the desktop bridge (a plain browser).
 *
 * Electron's preload reports backend status; a browser has no such events, so the backend's own
 * /health endpoint, reached through the same origin as every other request, is the status source
 * that lets project discovery start. Polling backs off while the backend is unreachable, keeps a
 * slow heartbeat once it is healthy, and needs two failed heartbeats in a row before a healthy
 * backend is reported lost (one dropped request must not force a project resync). Every request
 * has a timeout, so a hung request cannot stop the heartbeat. Cleanup aborts the in-flight request,
 * ignores a late answer and cancels the timer. Status is reported only when it changes.
 */
export interface BrowserBackendStatus {
  port: number | null;
  healthy: boolean;
  pid: null;
  device?: string;
  deviceName?: string;
}

export interface HealthReading {
  healthy: boolean;
  device?: string;
  deviceName?: string;
}

export interface BrowserHealthProbeOptions {
  /** Reads /health; may throw or reject when the backend is unreachable or the request is aborted. */
  check: (signal: AbortSignal) => Promise<HealthReading>;
  /** The port the renderer talks to, reported with a healthy status. */
  port: () => Promise<number>;
  onStatus: (status: BrowserBackendStatus) => void;
  initialDelayMs?: number;
  maxDelayMs?: number;
  healthyIntervalMs?: number;
  timeoutMs?: number;
  /** Consecutive failed heartbeats before a healthy backend is reported lost. */
  failuresBeforeLost?: number;
  schedule?: (callback: () => void, delayMs: number) => unknown;
  cancel?: (handle: unknown) => void;
  /** Arms the per-request timeout; returns its disarm function. */
  arm?: (onTimeout: () => void, timeoutMs: number) => () => void;
  warn?: (message: string) => void;
}

/** Only a renderer without the desktop bridge probes; an Electron window whose preload failed does not. */
export function needsBrowserHealthProbe(win: { api?: unknown; navigator?: { userAgent?: string } } | undefined): boolean {
  return Boolean(win) && !win!.api && !/\bElectron\//.test(win!.navigator?.userAgent ?? '');
}

export function startBrowserHealthProbe({
  check,
  port,
  onStatus,
  initialDelayMs = 500,
  maxDelayMs = 5000,
  healthyIntervalMs = 5000,
  timeoutMs = 4000,
  failuresBeforeLost = 2,
  schedule = (callback, delayMs) => setTimeout(callback, delayMs),
  cancel = handle => clearTimeout(handle as ReturnType<typeof setTimeout>),
  arm = (onTimeout, ms) => { const handle = setTimeout(onTimeout, ms); return () => clearTimeout(handle); },
  warn = message => console.warn(message),
}: BrowserHealthProbeOptions): () => void {
  let stopped = false;
  let timer: unknown = null;
  let controller: AbortController | null = null;
  let reported: BrowserBackendStatus | null = null;
  let failures = 0;
  let retryDelay = initialDelayMs;

  const same = (a: BrowserBackendStatus | null, b: BrowserBackendStatus) =>
    a !== null && a.healthy === b.healthy && a.port === b.port && a.device === b.device && a.deviceName === b.deviceName;

  const read = async (): Promise<BrowserBackendStatus> => {
    const current = new AbortController();
    controller = current;
    const disarm = arm(() => current.abort(new Error(`health check timed out after ${timeoutMs} ms`)), timeoutMs);
    try {
      const reading = await check(current.signal);
      if (!reading.healthy) throw new Error('backend reported unhealthy');
      return { port: await port(), healthy: true, pid: null, device: reading.device, deviceName: reading.deviceName };
    } finally {
      disarm();
      if (controller === current) controller = null;
    }
  };

  const probe = async (): Promise<void> => {
    timer = null;
    let next: BrowserBackendStatus;
    let problem: unknown = null;
    try {
      next = await read();
    } catch (error) {
      problem = error;
      next = { port: null, healthy: false, pid: null };
    }
    if (stopped) return;
    failures = next.healthy ? 0 : failures + 1;
    const lost = !next.healthy && (reported?.healthy !== true || failures >= failuresBeforeLost);
    if ((next.healthy || lost) && !same(reported, next)) {
      if (!next.healthy) warn(`Backend health check failed: ${problem instanceof Error ? problem.message : String(problem)}`);
      reported = next;
      onStatus(next);
      if (stopped) return;
    }
    let delay = healthyIntervalMs;
    if (next.healthy) {
      retryDelay = initialDelayMs;
    } else {
      delay = retryDelay;
      retryDelay = Math.min(retryDelay * 2, maxDelayMs);
    }
    timer = schedule(() => { void probe(); }, delay);
  };

  void probe();
  return () => {
    stopped = true;
    if (timer !== null) cancel(timer);
    timer = null;
    controller?.abort();
    controller = null;
  };
}
