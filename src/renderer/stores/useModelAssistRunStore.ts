/** Prevent project changes while a label proposal is being written or reviewed. */

import { create } from 'zustand';

interface ModelAssistRunState {
  activeOperations: number;
  begin: () => void;
  end: () => void;
}

export const useModelAssistRunStore = create<ModelAssistRunState>((set) => ({
  activeOperations: 0,
  begin: () => set((state) => ({ activeOperations: state.activeOperations + 1 })),
  end: () => set((state) => ({ activeOperations: Math.max(0, state.activeOperations - 1) })),
}));
