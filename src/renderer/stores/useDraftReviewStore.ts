import { create } from 'zustand';

/** How many images have machine drafts waiting for a person, as the draft review queue last read them.
 *  `scope` names the project, source, label set and task that count belongs to, so the footer never shows
 *  another project's count. */
interface DraftReviewState {
  scope: string;
  pendingImages: number;
  publish: (scope: string, pendingImages: number) => void;
}

export const useDraftReviewStore = create<DraftReviewState>((set) => ({
  scope: '',
  pendingImages: 0,
  publish: (scope, pendingImages) => set({ scope, pendingImages }),
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
