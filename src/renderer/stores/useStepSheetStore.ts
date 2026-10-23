import { create } from 'zustand';

/** Step 1's '데이터 관리 도구' sheets (image review and label exchange, service captures, derived versions) open one at a
 *  time, like step 2's right-side popovers (useDraftReviewStore.sidePanel): opening one names it here and the others
 *  close, so they never stack over each other and the gallery. */
export type StepSheet = 'workflow' | 'capture' | 'derived';

interface StepSheetState {
  open: StepSheet | null;
  show: (sheet: StepSheet) => void;
}

export const useStepSheetStore = create<StepSheetState>((set) => ({
  open: null,
  show: (open) => set({ open }),
}));
