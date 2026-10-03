import { create } from 'zustand';
import { request } from '../../services/api';
import type { DemoState, ExampleProjectRef, OnboardingState } from './firstRun';

// The first-run guide's state, kept outside the dialog so closing it never loses a running example's progress.
type FirstRunStore = {
  onboarding: OnboardingState | null;
  open: boolean;
  demo: DemoState | null;
  setOpen: (open: boolean) => void;
  setDemo: (demo: DemoState) => void;
  load: () => Promise<void>;
  dismiss: () => Promise<void>;
};

export const useFirstRunStore = create<FirstRunStore>((set, get) => ({
  onboarding: null,
  open: false,
  demo: null,
  setOpen: open => set({ open }),
  setDemo: demo => set({ demo }),
  load: async () => {
    try {
      const state = await request<{ version: 1; dismissed: boolean; team?: boolean; example_projects?: ExampleProjectRef[] }>('/api/onboarding');
      set({ onboarding: { version: 1, dismissed: Boolean(state.dismissed), team: Boolean(state.team), example_projects: state.example_projects || [],
        demo: get().demo ?? { state: 'idle', steps: {}, error: null, result: null } } });
    } catch {
      // An older backend or no answer: the guide does not open by itself; the header button still opens it.
      set({ onboarding: null });
    }
  },
  dismiss: async () => {
    await request('/api/onboarding/dismiss', { method: 'POST', body: JSON.stringify({ dismissed: true }) });
    const current = get().onboarding;
    set({ open: false, onboarding: current ? { ...current, dismissed: true } : current });
  },
}));
