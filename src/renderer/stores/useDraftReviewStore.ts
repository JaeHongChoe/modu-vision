import { create } from 'zustand';

/** Another panel's request to open one draft in the queue. `scope` is the project, source, label set and task it was
 *  asked in (the queue drops a request from another scope), `candidateIds` the candidates the person kept there, and
 *  the nonce, never reused, makes a repeated request for the same id count. */
export interface DraftOpenRequest { id: string; nonce: number; scope: string; candidateIds?: string[] }

/** How many images have machine drafts waiting for a person, and how many finished models fit the current data, as
 *  the draft review queue last read them. `scope` names the project, source, label set and task those numbers belong
 *  to, so the footer never shows another project's count. */
interface DraftReviewState {
  scope: string;
  pendingImages: number;
  /** Completed models of the task on this source (the ones that can draft); 0 until the queue has read them. */
  models: number;
  /** Cleared by the queue once it opened the draft, found it closed, or gave up on it. */
  openRequest: DraftOpenRequest | null;
  publish: (scope: string, pendingImages: number, models?: number) => void;
  /** `scope` defaults to the one the queue last published. */
  requestOpen: (id: string, candidateIds?: string[], scope?: string) => void;
  clearOpenRequest: () => void;
  /** Step 2's popovers (the draft queue, 모델 보조 and the image review sheet) open one at a time: opening one names it
   *  here and the others close, so none covers another. */
  sidePanel: 'drafts' | 'assist' | 'review' | null;
  showSidePanel: (panel: 'drafts' | 'assist' | 'review') => void;
  /** '모델 보조' asked for from elsewhere (an empty project's 'SAM2 클릭으로 그리기'): step 2 unfolds it for this visit
   *  even in focus editing, and the panel opens itself and clears the request. */
  assistRequest: number | null;
  requestAssist: () => void;
  clearAssistRequest: () => void;
}

let lastNonce = 0;

export const useDraftReviewStore = create<DraftReviewState>((set) => ({
  scope: '',
  pendingImages: 0,
  models: 0,
  openRequest: null,
  publish: (scope, pendingImages, models = 0) => set({ scope, pendingImages, models }),
  requestOpen: (id, candidateIds, scope) => set((state) => ({
    openRequest: { id, nonce: ++lastNonce, scope: scope ?? state.scope, ...(candidateIds ? { candidateIds: [...candidateIds] } : {}) },
  })),
  clearOpenRequest: () => set({ openRequest: null }),
  sidePanel: null,
  showSidePanel: (sidePanel) => set({ sidePanel }),
  assistRequest: null,
  requestAssist: () => set({ assistRequest: ++lastNonce }),
  clearAssistRequest: () => set({ assistRequest: null }),
}));

export function draftReviewScope(input: {
  projectDir?: string | null;
  project?: { id?: string; source_dataset_dir?: string | null; active_labelset_id?: string | null; task?: string } | null;
  task?: string | null;
}): string {
  const project = input.project;
  return JSON.stringify([input.projectDir || '', project?.id || '', project?.source_dataset_dir || '',
    project?.active_labelset_id || 'default', input.task || project?.task || '']);
}
