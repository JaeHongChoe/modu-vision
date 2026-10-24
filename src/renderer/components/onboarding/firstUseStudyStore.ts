import { create } from 'zustand';
import { getApiPersistenceIdentity, getProjectContext, getProjectContextGeneration, subscribeProjectContext } from '../../services/api';
import { useProjectStore } from '../../stores/useProjectStore';
import { readDatasetAuthority, useDatasetStore } from '../../stores/useDatasetStore';
import { createFirstUseStudy, type StudyStart, type StudySnapshot, type StudyMark, type StudyAbort } from './firstUseStudy';

/** Scope text exists only in this closure/controller. No scope, path or store object enters the exported schema. */
export function currentFirstUseStudyScope(): string | null {
  const p = useProjectStore.getState(), d = useDatasetStore.getState(), project = p.project;
  if (!project?.id || !p.projectDir || p.isProjectBusy || p.projectDir !== project.project_dir || p.task !== project.task
    || !d.hasSelectedFolder || !d.folderPath || d.folderPath !== project.source_dataset_dir
    || d.datasetKey !== `${d.folderPath}\0${p.task}`) return null;
  try {
    if (!d.sourceAuthority || d.sourceAuthority !== readDatasetAuthority()) return null;
    return JSON.stringify([project.id, p.projectDir, p.task, d.folderPath, d.datasetKey, project.active_labelset_id || 'default',
      d.sourceAuthority, getProjectContext(), getProjectContextGeneration(), getApiPersistenceIdentity()]);
  } catch { return null; }
}

export function subscribeFirstUseStudyScope(listener: () => void): () => void {
  const cleanup = [subscribeProjectContext(listener), useProjectStore.subscribe(listener), useDatasetStore.subscribe(listener)];
  return () => cleanup.forEach(stop => stop());
}

interface FirstUseStudyState {
  snapshot: Readonly<StudySnapshot> | null;
  history: ReadonlyArray<Readonly<StudySnapshot>>;
  start: (input: StudyStart) => boolean;
  bind: (scope: string | null) => boolean;
  checkScope: (scope: string | null) => void;
  count: (kind: 'pointer' | 'keyboard', recorderControl?: boolean) => void;
  mark: (kind: StudyMark) => void;
  reportExternalActions: (count: number) => boolean;
  stop: () => void;
  abort: (reason: StudyAbort) => void;
  tick: () => void;
  exportReport: () => { schema_version: 1; acceptance: 'unverified'; sessions: ReadonlyArray<Readonly<StudySnapshot>> };
}

/** In memory only. No persistence middleware and no backend/provider requests. */
export function createFirstUseStudyStore(now?: () => number) {
  const recorder = createFirstUseStudy(now);
  return create<FirstUseStudyState>((set, get) => {
    const update = () => {
      const value = recorder.snapshot(), previous = get().snapshot;
      if (!value) return;
      const finished = previous?.status === 'recording' && value.status !== 'recording';
      set({ snapshot: value, ...(finished ? { history: Object.freeze([...get().history, value].slice(-20)) } : {}) });
    };
    return { snapshot: null, history: Object.freeze([]),
      start: input => { const started = recorder.start(input); if (started) update(); return started; },
      bind: scope => { const bound = recorder.bind(scope); update(); return bound; },
      checkScope: scope => { recorder.checkScope(scope); update(); },
      count: (kind, own) => { if (recorder.count(kind, own)) update(); },
      mark: kind => { if (recorder.mark(kind)) update(); },
      reportExternalActions: count => { const changed = recorder.reportExternalActions(count); if (changed) update(); return changed; },
      stop: () => { if (recorder.stop()) update(); },
      abort: reason => { if (recorder.abort(reason)) update(); },
      tick: () => { if (get().snapshot?.status === 'recording') update(); },
      exportReport: () => {
        update();
        const value = get().snapshot;
        const sessions = value?.status === 'recording' ? [...get().history, value] : [...get().history];
        return Object.freeze({ schema_version: 1, acceptance: 'unverified', sessions: Object.freeze(sessions) });
      },
    };
  });
}

export const useFirstUseStudyStore = createFirstUseStudyStore();
