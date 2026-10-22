import { create } from 'zustand';

/** How many images have machine drafts waiting for a person, and how many finished models fit the current data, as
 *  the draft review queue last read them. `scope` names the project, source, label set and task those numbers belong
 *  to, so the footer never shows another project's count. */
interface DraftReviewState {
  scope: string;
  pendingImages: number;
  /** Models trained on exactly the current data (the ones that can draft); 0 until the queue has read them. */
  models: number;
  publish: (scope: string, pendingImages: number, models?: number) => void;
}

export const useDraftReviewStore = create<DraftReviewState>((set) => ({
  scope: '',
  pendingImages: 0,
  models: 0,
  publish: (scope, pendingImages, models = 0) => set({ scope, pendingImages, models }),
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
