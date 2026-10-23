/** Open/close state and the conversation of the built-in assistant panel (components/assistant/AssistantPanel.tsx).
 *
 * Mount `<AssistantPanel />` once and call `useAssistantStore.getState().open()` from any button. Nothing here calls out:
 * the panel reads the provider settings from the main process only when opened, and a provider is called only when the
 * person configured one and pressed send. A key never enters this store (the key field sends it straight to the main
 * process and clears itself). */
import { create } from 'zustand';
import type {
  AssistantBridge, AssistantConfigState, AssistantError, AssistantProviderId, AssistantProviderInput,
} from '../../types/electron';
import { AssistantConversation, type PendingWrite, type SessionEvent } from '../services/assistantSession';
import { assistantLog, type RunRecord } from '../services/assistantLog';
import { assistantContext, createRuntimeTools, currentImageForQuestion } from '../services/assistantRuntime';
import { useAnnotationStore } from './useAnnotationStore';
import { host } from '../services/hostAdapter';

export interface AssistantChatItem { id: number; kind: 'person' | 'assistant' | 'tool' | 'notice' | 'error'; text: string; detail?: string; ok?: boolean }
export interface AssistantImageOffer { grantId: string; message: string; providerLabel: string; question: string }

interface AssistantState {
  isOpen: boolean;
  open: () => void;
  close: () => void;
  toggle: () => void;
  config: AssistantConfigState | null;
  configError: string | null;
  items: AssistantChatItem[];
  busy: boolean;
  pending: PendingWrite | null;
  imageOffer: AssistantImageOffer | null;
  runs: RunRecord[] | null;
  runsError: string | null;
  loadConfig: () => Promise<void>;
  saveProvider: (input: AssistantProviderInput) => Promise<boolean>;
  removeKey: (provider: AssistantProviderId) => Promise<void>;
  removeProvider: (provider: AssistantProviderId) => Promise<void>;
  setActive: (provider: AssistantProviderId) => Promise<void>;
  listModels: (provider: AssistantProviderId) => Promise<{ id: string; label: string }[] | AssistantError>;
  ask: (text: string) => Promise<void>;
  stop: () => Promise<void>;
  decide: (confirmed: boolean) => Promise<void>;
  offerImage: (question: string) => Promise<void>;
  sendImage: () => Promise<void>;
  dropImage: () => void;
  newConversation: () => void;
  loadRuns: () => Promise<void>;
  archiveRuns: () => Promise<void>;
}

export function assistantBridge(): AssistantBridge | null {
  return host.assistant();
}

const message = (cause: unknown) => (cause instanceof Error ? cause.message.replace(/^Error invoking remote method '[^']+': (Error: )?/, '') : String(cause));
let nextItem = 0;
let conversation: AssistantConversation | null = null;

export const useAssistantStore = create<AssistantState>((set, get) => {
  const add = (item: Omit<AssistantChatItem, 'id'>) => set(state => ({ items: [...state.items, { ...item, id: ++nextItem }].slice(-200) }));
  const onEvent = (event: SessionEvent) => {
    if (event.kind === 'assistant') add({ kind: 'assistant', text: event.text });
    else if (event.kind === 'tool') add({ kind: 'tool', text: event.summary, detail: event.name, ok: event.ok });
    else if (event.kind === 'notice') add({ kind: 'notice', text: event.text });
    else if (event.kind === 'error') add({ kind: 'error', text: event.error.reason, detail: [event.error.action, event.error.requestId ? `요청 ID ${event.error.requestId}` : ''].filter(Boolean).join(' ') });
    else if (event.kind === 'busy') set({ busy: event.busy });
    else if (event.kind === 'confirm') set({ pending: event.pending });
  };
  const session = (): AssistantConversation | null => {
    const bridge = assistantBridge();
    if (!bridge) return null;
    if (!conversation) {
      conversation = new AssistantConversation({
        bridge, tools: createRuntimeTools(), context: assistantContext,
        log: entry => assistantLog.append(entry), provider: () => get().config?.active ?? null,
      }, onEvent);
    }
    return conversation;
  };
  const configCall = async (run: (bridge: AssistantBridge) => Promise<AssistantConfigState>): Promise<boolean> => {
    const bridge = assistantBridge();
    if (!bridge) { set({ configError: '이 화면에서는 도우미를 쓸 수 없습니다. 데스크톱 앱에서 여세요.' }); return false; }
    try { set({ config: await run(bridge), configError: null }); return true; }
    catch (cause) { set({ configError: message(cause) }); return false; }
  };
  return {
    isOpen: false,
    open: () => { set({ isOpen: true }); if (!get().config) void get().loadConfig(); },
    close: () => set({ isOpen: false }),
    toggle: () => (get().isOpen ? get().close() : get().open()),
    config: null,
    configError: null,
    items: [],
    busy: false,
    pending: null,
    imageOffer: null,
    runs: null,
    runsError: null,
    loadConfig: async () => { await configCall(bridge => bridge.getState()); },
    saveProvider: input => configCall(bridge => bridge.saveProvider(input)),
    removeKey: async provider => { await configCall(bridge => bridge.removeKey(provider)); },
    removeProvider: async provider => { await configCall(bridge => bridge.removeProvider(provider)); },
    setActive: async provider => { await configCall(bridge => bridge.setActive(provider)); },
    listModels: async provider => {
      const bridge = assistantBridge();
      if (!bridge) return { code: 'no_provider', reason: '이 화면에서는 도우미를 쓸 수 없습니다.', action: '데스크톱 앱에서 여세요.' };
      const result = await bridge.listModels(provider);
      return result.ok ? result.models : result.error;
    },
    ask: async text => {
      const active = session();
      if (!active || !text.trim()) return;
      add({ kind: 'person', text: text.trim() });
      await active.ask(text);
    },
    stop: async () => { await conversation?.stop(); },
    decide: async confirmed => { await conversation?.decide(confirmed); },
    offerImage: async question => {
      const bridge = assistantBridge();
      if (!bridge || !question.trim()) return;
      const image = useAnnotationStore.getState().currentImage;
      if (!image) { add({ kind: 'notice', text: '2단계에서 이미지를 연 뒤 이미지와 함께 물어보세요.' }); return; }
      const grant = await bridge.imageIntent({ fileName: image.file_name });
      if (!grant.ok) { add({ kind: 'error', text: grant.error.reason, detail: grant.error.action }); return; }
      set({ imageOffer: { grantId: grant.grantId, message: grant.message, providerLabel: grant.providerLabel, question: question.trim() } });
    },
    sendImage: async () => {
      // Only this action, after the person read the offer and pressed send, reads the image and lets it leave.
      const offer = get().imageOffer;
      set({ imageOffer: null });
      const active = session();
      if (!offer || !active) return;
      try {
        const image = await currentImageForQuestion();
        if (!image) { add({ kind: 'notice', text: '열린 이미지가 없어 보내지 않았습니다.' }); return; }
        add({ kind: 'person', text: `${offer.question} (이미지 1장)` });
        await active.ask(offer.question, { grantId: offer.grantId, ...image });
      } catch (cause) {
        add({ kind: 'error', text: '이미지를 읽지 못해 보내지 않았습니다.', detail: message(cause) });
      }
    },
    dropImage: () => set({ imageOffer: null }),
    newConversation: () => {
      if (get().busy || get().pending) return;
      conversation = null;
      set({ items: [], imageOffer: null });
    },
    loadRuns: async () => {
      try { const listing = await assistantLog.list(); set({ runs: listing.runs.slice().reverse(), runsError: null }); }
      catch (cause) { set({ runsError: message(cause) }); }
    },
    archiveRuns: async () => {
      try { await assistantLog.archive(); await get().loadRuns(); }
      catch (cause) { set({ runsError: message(cause) }); }
    },
  };
});
