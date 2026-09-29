import { create } from 'zustand';

/** Shared guard so a project cannot switch while a batch writes its history. */
export const useInspectionRunStore = create<{
  isRunning: boolean;
  setRunning: (value: boolean) => void;
}>((set) => ({
  isRunning: false,
  setRunning: (isRunning) => set({ isRunning }),
}));
