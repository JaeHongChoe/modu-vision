/** Opt-in local measurement, with no DOM, storage, network or project dependencies.
 * A mode/goal is a declaration, never proof of a novice trial or acceptance. */
export type StudyMode = 'human_self_reported' | 'automated';
export type StudyTask = 'first_flow' | 'manual_label' | 'assisted_draft';
export type StudyMark = 'image_complete_self_reported' | 'flow_saved_self_reported' | 'help_self_reported';
export type StudyAbort = 'scope_changed' | 'scope_unavailable' | 'observer_removed' | 'page_unloaded';
export interface StudyStart { mode: StudyMode; task: StudyTask; build?: unknown }
export interface StudyBuild {
  app_version: string | null;
  platform: 'darwin' | 'win32' | 'linux' | 'browser' | 'unknown';
  architecture: 'arm64' | 'x64' | 'ia32' | null;
  /** No existing renderer build-SHA hook: an app version must never be substituted for it. */
  build_sha: null;
}
export interface StudySnapshot {
  schema_version: 1;
  mode: StudyMode;
  task: StudyTask;
  status: 'recording' | 'completed' | 'aborted';
  reason: StudyAbort | null;
  acceptance: 'unverified';
  target_assessment: 'unverified';
  started_at_ms: number;
  elapsed_ms: number;
  binding: 'unbound' | 'bound';
  workflow_actions: { pointer: number; keyboard: number };
  recorder_actions: { pointer: number; keyboard: number };
  external_actions: { source: 'unobserved' | 'self_reported'; count: number | null };
  images_completed_self_reported: number;
  elapsed_ms_per_image: number | null;
  milestones: ReadonlyArray<Readonly<{ kind: StudyMark | 'scope_bound'; elapsed_ms: number }>>;
  milestones_truncated: boolean;
  clock_anomaly: boolean;
  build: StudyBuild;
}

function projectBuild(input: unknown): StudyBuild {
  const value = input && typeof input === 'object' ? input as Record<string, unknown> : {};
  // Only the app's version grammar and fixed environment enums may leave this module.
  const version = typeof value.app_version === 'string' && /^\d{1,4}\.\d{1,4}\.\d{1,4}(?:-(?:alpha|beta|rc)\.\d{1,4})?$/.test(value.app_version)
    ? value.app_version : null;
  const platform = typeof value.platform === 'string' && ['darwin', 'win32', 'linux', 'browser'].includes(value.platform) ? value.platform as StudyBuild['platform'] : 'unknown';
  const architecture = typeof value.architecture === 'string' && ['arm64', 'x64', 'ia32'].includes(value.architecture) ? value.architecture as StudyBuild['architecture'] : null;
  return Object.freeze({ app_version: version, platform, architecture, build_sha: null });
}

export function createFirstUseStudy(now: () => number = () => Date.now()) {
  let current: StudySnapshot | null = null;
  let boundScope: string | null = null; // private, never spread into a snapshot or error
  let lastClock = 0;
  let sealed: Readonly<StudySnapshot> | null = null;
  const active = () => current?.status === 'recording';
  const refreshTime = () => {
    if (!active() || !current) return;
    const clock = now();
    if (!Number.isFinite(clock) || clock < lastClock) current.clock_anomaly = true;
    if (Number.isFinite(clock)) {
      current.elapsed_ms = Math.max(current.elapsed_ms, clock - current.started_at_ms);
      lastClock = clock;
    }
    current.elapsed_ms_per_image = current.images_completed_self_reported ? current.elapsed_ms / current.images_completed_self_reported : null;
  };
  const snapshot = (): Readonly<StudySnapshot> | null => {
    if (sealed) return sealed;
    if (!current) return null;
    refreshTime();
    return Object.freeze({ ...current,
      workflow_actions: Object.freeze({ ...current.workflow_actions }),
      recorder_actions: Object.freeze({ ...current.recorder_actions }),
      external_actions: Object.freeze({ ...current.external_actions }),
      milestones: Object.freeze(current.milestones.map(row => Object.freeze({ ...row }))),
      build: current.build,
    });
  };
  const milestone = (kind: StudyMark | 'scope_bound') => {
    if (!current) return;
    refreshTime();
    if (current.milestones.length < 256) current.milestones = [...current.milestones, { kind, elapsed_ms: current.elapsed_ms }];
    else current.milestones_truncated = true;
  };
  const finish = (status: 'completed' | 'aborted', reason: StudyAbort | null) => {
    if (!active() || !current) return false;
    refreshTime(); current.status = status; current.reason = reason;
    boundScope = null;
    sealed = snapshot();
    return true;
  };
  return {
    start(input: StudyStart): boolean {
      if (active() || !['human_self_reported', 'automated'].includes(input.mode)
        || !['first_flow', 'manual_label', 'assisted_draft'].includes(input.task)) return false;
      const clock = now();
      const start = Number.isFinite(clock) && clock >= 0 ? clock : 0;
      boundScope = null; sealed = null; lastClock = start;
      current = { schema_version: 1, mode: input.mode, task: input.task, status: 'recording', reason: null,
        acceptance: 'unverified', target_assessment: 'unverified', started_at_ms: start, elapsed_ms: 0, binding: 'unbound',
        workflow_actions: { pointer: 0, keyboard: 0 }, recorder_actions: { pointer: 0, keyboard: 0 },
        external_actions: { source: 'unobserved', count: null }, images_completed_self_reported: 0, elapsed_ms_per_image: null,
        milestones: [], milestones_truncated: false, clock_anomaly: start !== clock, build: projectBuild(input.build) };
      return true;
    },
    bind(scope: string | null): boolean {
      if (!active() || !current || boundScope !== null || typeof scope !== 'string' || !scope) return false;
      boundScope = scope; current.binding = 'bound'; milestone('scope_bound'); return true;
    },
    checkScope(scope: string | null): void {
      if (active() && boundScope !== null && scope !== boundScope) finish('aborted', scope === null ? 'scope_unavailable' : 'scope_changed');
    },
    count(kind: 'pointer' | 'keyboard', recorderControl = false): boolean {
      if (!active() || !current || !['pointer', 'keyboard'].includes(kind)) return false;
      const counts = recorderControl ? current.recorder_actions : current.workflow_actions;
      counts[kind] = Math.min(Number.MAX_SAFE_INTEGER, counts[kind] + 1); return true;
    },
    reportExternalActions(count: number): boolean {
      if (!active() || !current || !Number.isSafeInteger(count) || count < 0) return false;
      current.external_actions = { source: 'self_reported', count }; return true;
    },
    mark(kind: StudyMark): boolean {
      if (!active() || !current || !['image_complete_self_reported', 'flow_saved_self_reported', 'help_self_reported'].includes(kind)) return false;
      if (kind === 'image_complete_self_reported') {
        if (current.task === 'first_flow') return false;
        current.images_completed_self_reported = Math.min(Number.MAX_SAFE_INTEGER, current.images_completed_self_reported + 1);
      }
      milestone(kind); return true;
    },
    abort(reason: StudyAbort): boolean {
      return ['scope_changed', 'scope_unavailable', 'observer_removed', 'page_unloaded'].includes(reason) ? finish('aborted', reason) : false;
    },
    stop: () => finish('completed', null),
    snapshot,
  };
}
