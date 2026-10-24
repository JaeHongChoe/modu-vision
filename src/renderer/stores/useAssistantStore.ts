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
import { assistantScope, assistantImageBinding, assistantWriteBinding } from '../services/assistantAuthority';
import { sha256Hex } from '../services/assistantContext';
import { useProjectStore } from './useProjectStore';
import { useComputeStore } from './useComputeStore';
import { useDatasetStore } from './useDatasetStore';
import { useTrainingStore } from './useTrainingStore';
import { subscribeProjectContext } from '../services/api';
import { host } from '../services/hostAdapter';

export interface AssistantChatItem { id: number; kind: 'person' | 'assistant' | 'tool' | 'notice' | 'error'; text: string; detail?: string; ok?: boolean; code?: AssistantError['code'] }
export interface AssistantImageOffer { grantId: string; message: string; providerLabel: string; question: string; binding: string; imageIdentity: string }

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
  beginChatgpt: (input: { termsAccepted: true; recordId?: string; label?: string }) => Promise<boolean>;
  cancelChatgpt: () => Promise<boolean>;
  selectChatgptAccount: (recordId: string | null) => Promise<boolean>;
  selectChatgptModel: (recordId: string, model: string) => Promise<boolean>;
  signOutChatgpt: (recordId: string) => Promise<boolean>;
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
let imageOfferVersion = 0;
let configRequest = 0;
let configuring = false;
let imageRevision = 0;
let writeRevision = 0;
const readImageBinding = () => JSON.stringify([imageRevision, assistantImageBinding(assistantScope(useAssistantStore.getState().config))]);
const readWriteBinding = () => JSON.stringify([writeRevision, assistantWriteBinding(assistantScope(useAssistantStore.getState().config))]);

export const useAssistantStore = create<AssistantState>((set, get) => {
  const add = (item: Omit<AssistantChatItem, 'id'>) => set(state => ({ items: [...state.items, { ...item, id: ++nextItem }].slice(-200) }));
  const onEvent = (event: SessionEvent) => {
    if (event.kind === 'assistant') add({ kind: 'assistant', text: event.text });
    else if (event.kind === 'tool') add({ kind: 'tool', text: event.summary, detail: event.name, ok: event.ok });
    else if (event.kind === 'notice') add({ kind: 'notice', text: event.text });
    else if (event.kind === 'error') add({ kind: 'error', code: event.error.code, text: event.error.reason, detail: [event.error.action, event.error.requestId ? `요청 ID ${event.error.requestId}` : ''].filter(Boolean).join(' ') });
    else if (event.kind === 'busy') set({ busy: event.busy });
    else if (event.kind === 'confirm') set({ pending: event.pending });
  };
  const session = (): AssistantConversation | null => {
    const bridge = assistantBridge();
    if (!bridge) return null;
    if (!conversation) {
      conversation = new AssistantConversation({
        bridge, tools: createRuntimeTools(), context: assistantContext,
        scope: () => assistantScope(get().config),
        writeBinding: readWriteBinding,
        log: entry => assistantLog.append(entry), provider: () => get().config?.active ?? null,
      }, onEvent);
    }
    return conversation;
  };
  const configCall = async (run: (bridge: AssistantBridge) => Promise<AssistantConfigState>): Promise<boolean> => {
    const bridge = assistantBridge();
    if (!bridge) { set({ configError: '이 화면에서는 도우미를 쓸 수 없습니다. 데스크톱 앱에서 여세요.' }); return false; }
    const request = ++configRequest; configuring = true;
    try { const config = await run(bridge); if (request !== configRequest) return false; set({ config, configError: null }); return true; }
    catch (cause) { if (request === configRequest) set({ configError: message(cause) }); return false; }
    finally { if (request === configRequest) configuring = false; }
  };
  const chatgptCall = (run: (connection: NonNullable<AssistantBridge['chatgpt']>) => Promise<AssistantConfigState>) => {
    invalidateAssistant();
    return configCall(bridge => {
      if (!bridge.chatgpt) throw new Error('이 앱에서는 ChatGPT 연결을 지원하지 않습니다. 최신 데스크톱 앱에서 다시 여세요.');
      return run(bridge.chatgpt);
    });
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
    saveProvider: async input => { invalidateAssistant(); return configCall(bridge => bridge.saveProvider(input)); },
    removeKey: async provider => { invalidateAssistant(); await configCall(bridge => bridge.removeKey(provider)); },
    removeProvider: async provider => { invalidateAssistant(); await configCall(bridge => bridge.removeProvider(provider)); },
    setActive: async provider => { invalidateAssistant(); await configCall(bridge => bridge.setActive(provider)); },
    beginChatgpt: input => chatgptCall(connection => connection.begin(input)),
    cancelChatgpt: () => chatgptCall(connection => connection.cancel()),
    selectChatgptAccount: recordId => chatgptCall(connection => connection.selectAccount(recordId)),
    selectChatgptModel: (recordId, model) => chatgptCall(connection => connection.selectModel(recordId, model)),
    signOutChatgpt: recordId => chatgptCall(connection => connection.signOut(recordId)),
    listModels: async provider => {
      const bridge = assistantBridge();
      if (!bridge) return { code: 'no_provider', reason: '이 화면에서는 도우미를 쓸 수 없습니다.', action: '데스크톱 앱에서 여세요.' };
      const result = await bridge.listModels(provider);
      return result.ok ? result.models : result.error;
    },
    ask: async text => {
      const active = session();
      if (configuring || !active || !text.trim() || active.busy || active.pending) return;
      add({ kind: 'person', text: text.trim() });
      await active.ask(text);
    },
    stop: async () => { await conversation?.stop(); },
    decide: async confirmed => { await conversation?.decide(confirmed); },
    offerImage: async question => {
      const bridge = assistantBridge();
      if (configuring || !bridge || !question.trim()) return;
      if (get().config?.active === 'chatgpt') {
        set({ imageOffer: null });
        add({ kind: 'notice', text: 'ChatGPT 연결은 현재 텍스트 질문만 지원합니다. 이미지 질문은 이미지 지원 API 키 공급자를 선택한 뒤 전송 대상을 확인하세요.' });
        return;
      }
      const version = ++imageOfferVersion;
      set({ imageOffer: null });
      const binding = readImageBinding();
      const image = useAnnotationStore.getState().currentImage;
      if (!image) { add({ kind: 'notice', text: '2단계에서 이미지를 연 뒤 이미지와 함께 물어보세요.' }); return; }
      const current = () => version === imageOfferVersion && binding === readImageBinding();
      try {
        const imageIdentity = await sha256Hex(binding);
        if (!current()) return;
        const grant = await bridge.imageIntent({ fileName: image.file_name, imageIdentity });
        if (!current()) return;
        if (!grant.ok) { add({ kind: 'error', text: grant.error.reason, detail: grant.error.action }); return; }
        set({ imageOffer: { grantId: grant.grantId, message: grant.message, providerLabel: grant.providerLabel, question: question.trim(), binding, imageIdentity } });
      } catch {
        if (current()) add({kind: 'error', text: '이미지 전송 확인을 준비하지 못했습니다.', detail: '도우미 연결과 수신 설정을 확인한 뒤 다시 요청하세요.'});
      }
    },
    sendImage: async () => {
      // Only this action, after the person read the offer and pressed send, reads the image and lets it leave.
      const offer = get().imageOffer;
      set({ imageOffer: null });
      const version = ++imageOfferVersion;
      const active = session();
      if (configuring || !offer || !active || active.busy || active.pending) return;
      const current = () => version === imageOfferVersion && offer.binding === readImageBinding();
      if (!current()) { add({ kind: 'notice', text: '이미지 또는 수신 설정이 바뀌어 보내지 않았습니다. 다시 전송 대상을 확인하세요.' }); return; }
      try {
        const image = await currentImageForQuestion();
        if (!current()) { add({ kind: 'notice', text: '이미지 또는 수신 설정이 바뀌어 보내지 않았습니다. 다시 전송 대상을 확인하세요.' }); return; }
        if (!image) { add({ kind: 'notice', text: '열린 이미지가 없어 보내지 않았습니다.' }); return; }
        add({ kind: 'person', text: `${offer.question} (이미지 1장)` });
        await active.ask(offer.question, { grantId: offer.grantId, imageIdentity: offer.imageIdentity, ...image });
      } catch (cause) {
        if (current()) add({ kind: 'error', text: '이미지를 읽지 못해 보내지 않았습니다.', detail: message(cause) });
      }
    },
    dropImage: () => { imageOfferVersion++; set({ imageOffer: null }); },
    newConversation: () => {
      if (get().busy || get().pending) return;
      imageOfferVersion++; conversation = null;
      set({ items: [], imageOffer: null });
    },
    loadRuns: async () => {
      const scope = assistantScope(get().config);
      try { const listing = await assistantLog.list(); if (scope === assistantScope(get().config)) set({ runs: listing.runs.slice().reverse(), runsError: null }); }
      catch (cause) { if (scope === assistantScope(get().config)) set({ runsError: message(cause) }); }
    },
    archiveRuns: async () => {
      const scope = assistantScope(get().config);
      try { await assistantLog.archive(); if (scope === assistantScope(get().config)) await get().loadRuns(); }
      catch (cause) { if (scope === assistantScope(get().config)) set({ runsError: message(cause) }); }
    },
  };
});

/** Reset immediately on accepted project/transport changes, including A → B → A. */
let observedScope = assistantScope(useAssistantStore.getState().config);
let observedImage = assistantImageBinding(observedScope);
let observedWrite = assistantWriteBinding(observedScope);
function invalidateAssistant(): void {
  imageOfferVersion++;
  conversation?.invalidate();
  conversation = null;
  useAssistantStore.setState({ pending: null, busy: false, imageOffer: null, items: [], runs: null, runsError: null });
}
function observeAuthority(): void {
  const next = assistantScope(useAssistantStore.getState().config);
  const image = assistantImageBinding(next), write = assistantWriteBinding(next);
  if (image !== observedImage) { observedImage = image; imageRevision++; imageOfferVersion++; }
  if (write !== observedWrite) { observedWrite = write; writeRevision++; }
  if (next !== observedScope) { observedScope = next; invalidateAssistant(); }
  else {
    const pending = useAssistantStore.getState().pending;
    if (pending && pending.binding !== readWriteBinding()) {
      conversation?.invalidate('이미지 또는 설정이 바뀌어 이전 실행 확인을 취소했습니다. 현재 화면에서 다시 요청하세요.');
    }
    const offer = useAssistantStore.getState().imageOffer;
    if (offer && offer.binding !== readImageBinding()) { imageOfferVersion++; useAssistantStore.setState({ imageOffer: null }); }
  }
}
useProjectStore.subscribe(observeAuthority);
useComputeStore.subscribe(observeAuthority);
useAnnotationStore.subscribe(observeAuthority);
useDatasetStore.subscribe(observeAuthority);
useTrainingStore.subscribe(observeAuthority);
useAssistantStore.subscribe(state => {
  const next = assistantScope(state.config);
  if (next !== observedScope) observeAuthority();
});
subscribeProjectContext(observeAuthority);
