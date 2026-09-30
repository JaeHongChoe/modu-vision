import { create } from 'zustand';
import { api } from '../services/api';
import type { ComputeProfile, ComputeProfileInput, ComputeProbeResult } from '../services/api';

interface ComputeState {
  transportRevision:number;
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
let transportEpoch=0;
export function resetComputeTransportCache():void {
  transportEpoch+=1;loadingPromise=null;
  useComputeStore.setState({transportRevision:transportEpoch,profiles:[],selectedProfileId:null,isLoaded:false,isLoading:false,isSaving:false,probePendingId:null,loadError:null,error:null,probeResults:{}});
}

export const useComputeStore = create<ComputeState>((set, get) => ({
  transportRevision:0,
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
    const epoch=transportEpoch;
    loadingPromise = (async () => {
      try {
        const [profileResponse, selectionResponse] = await Promise.all([
          api.compute.listProfiles(), api.compute.getSelection(),
        ]);
        if(epoch!==transportEpoch)return;
        set({
          profiles: profileResponse.profiles,
          selectedProfileId: selectionResponse.compute_profile_id,
          isLoaded: true,
          isLoading: false,
          error: null,
        });
      } catch (error) {
        if(epoch!==transportEpoch)return;
        set({ isLoaded: false, isLoading: false, loadError: errorMessage(error) });
        throw error;
      }
    })().finally(() => { if(epoch===transportEpoch)loadingPromise = null; });
    return loadingPromise;
  },

  saveProfile: async (profile) => {
    const epoch=transportEpoch;
    set({ isSaving: true, error: null });
    try {
      const { profile: saved } = await api.compute.saveProfile(profile);
      if(epoch!==transportEpoch)throw new Error('서버 연결이 변경됐습니다. 현재 서버에서 다시 확인하세요.');
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
      if(epoch===transportEpoch)set({ error: errorMessage(error) });
      throw error;
    } finally {
      if(epoch===transportEpoch)set({ isSaving: false });
    }
  },

  deleteProfile: async (id) => {
    const epoch=transportEpoch;
    set({ error: null });
    try {
      await api.compute.deleteProfile(id);
      const selection = await api.compute.getSelection();
      if(epoch!==transportEpoch)return;
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
      if(epoch===transportEpoch)set({ error: errorMessage(error) });
      throw error;
    }
  },

  selectTarget: async (id) => {
    const epoch=transportEpoch;
    if (id !== null && !get().profiles.some((profile) => profile.id === id)) {
      throw new Error('선택한 서버 설정을 찾을 수 없습니다.');
    }
    set({ error: null });
    try {
      const selection = await api.compute.selectProfile(id);
      if(epoch!==transportEpoch)return;
      set({ selectedProfileId: selection.compute_profile_id });
    } catch (error) {
      if(epoch===transportEpoch)set({ error: errorMessage(error) });
      throw error;
    }
  },

  probeProfile: async (id) => {
    const epoch=transportEpoch;
    set({ probePendingId: id, error: null });
    try {
      const result = await api.compute.probeProfile(id);
      if(epoch!==transportEpoch)throw new Error('서버 연결이 변경됐습니다. 현재 서버에서 다시 확인하세요.');
      set((state) => ({ probeResults: { ...state.probeResults, [id]: result } }));
      return result;
    } catch (error) {
      const message = errorMessage(error);
      if(epoch===transportEpoch)set((state) => ({
        error: message,
        probeResults: { ...state.probeResults, [id]: { ready: false, message } },
      }));
      throw error;
    } finally {
      if(epoch===transportEpoch)set({ probePendingId: null });
    }
  },

  getSelectedProfile: () => get().profiles.find((profile) => profile.id === get().selectedProfileId),
}));
