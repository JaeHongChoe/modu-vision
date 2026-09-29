import { create } from 'zustand';
import { api } from '../services/api';
import type { ComputeProfile, ComputeProfileInput, ComputeProbeResult } from '../services/api';

interface ComputeState {
  profiles: ComputeProfile[];
  selectedProfileId: string | null;
  isLoaded: boolean;
  isLoading: boolean;
  isSaving: boolean;
  probePendingId: string | null;
  loadError: string | null;
  error: string | null;
  probeResults: Record<string, ComputeProbeResult>;
  load: () => Promise<void>;
  saveProfile: (profile: ComputeProfileInput) => Promise<ComputeProfile>;
  deleteProfile: (id: string) => Promise<void>;
  selectTarget: (id: string | null) => Promise<void>;
  probeProfile: (id: string) => Promise<ComputeProbeResult>;
  getSelectedProfile: () => ComputeProfile | undefined;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  return '서버 설정 요청을 완료할 수 없습니다.';
}

let loadingPromise: Promise<void> | null = null;

export const useComputeStore = create<ComputeState>((set, get) => ({
  profiles: [],
  selectedProfileId: null,
  isLoaded: false,
  isLoading: false,
  isSaving: false,
  probePendingId: null,
  loadError: null,
  error: null,
  probeResults: {},

  load: async () => {
    if (loadingPromise) return loadingPromise;
    set({ isLoading: true, loadError: null });
    loadingPromise = (async () => {
      try {
        const [profileResponse, selectionResponse] = await Promise.all([
          api.compute.listProfiles(), api.compute.getSelection(),
        ]);
        set({
          profiles: profileResponse.profiles,
          selectedProfileId: selectionResponse.compute_profile_id,
          isLoaded: true,
          isLoading: false,
          error: null,
        });
      } catch (error) {
        set({ isLoaded: false, isLoading: false, loadError: errorMessage(error) });
        throw error;
      }
    })().finally(() => { loadingPromise = null; });
    return loadingPromise;
  },

  saveProfile: async (profile) => {
    set({ isSaving: true, error: null });
    try {
      const { profile: saved } = await api.compute.saveProfile(profile);
      set((state) => {
        const probeResults = { ...state.probeResults };
        delete probeResults[saved.id];
        return {
          profiles: [...state.profiles.filter((item) => item.id !== saved.id), saved],
          probeResults,
        };
      });
      return saved;
    } catch (error) {
      set({ error: errorMessage(error) });
      throw error;
    } finally {
      set({ isSaving: false });
    }
  },

  deleteProfile: async (id) => {
    set({ error: null });
    try {
      await api.compute.deleteProfile(id);
      const selection = await api.compute.getSelection();
      set((state) => {
        const probeResults = { ...state.probeResults };
        delete probeResults[id];
        return {
          profiles: state.profiles.filter((item) => item.id !== id),
          selectedProfileId: selection.compute_profile_id,
          probeResults,
        };
      });
    } catch (error) {
      set({ error: errorMessage(error) });
      throw error;
    }
  },

  selectTarget: async (id) => {
    if (id !== null && !get().profiles.some((profile) => profile.id === id)) {
      throw new Error('선택한 서버 설정을 찾을 수 없습니다.');
    }
    set({ error: null });
    try {
      const selection = await api.compute.selectProfile(id);
      set({ selectedProfileId: selection.compute_profile_id });
    } catch (error) {
      set({ error: errorMessage(error) });
      throw error;
    }
  },

  probeProfile: async (id) => {
    set({ probePendingId: id, error: null });
    try {
      const result = await api.compute.probeProfile(id);
      set((state) => ({ probeResults: { ...state.probeResults, [id]: result } }));
      return result;
    } catch (error) {
      const message = errorMessage(error);
      set((state) => ({
        error: message,
        probeResults: { ...state.probeResults, [id]: { ready: false, message } },
      }));
      throw error;
    } finally {
      set({ probePendingId: null });
    }
  },

  getSelectedProfile: () => get().profiles.find((profile) => profile.id === get().selectedProfileId),
}));
