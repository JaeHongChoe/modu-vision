/** The built-in assistant (docs/product-direction-easy-labeling-agent.md §4): a side panel that explains where the
 * person is stuck, opens the right screen, and runs the app's own tools — writes only after the person reads the preview
 * and confirms here. Mount it once; `useAssistantStore.getState().open()` shows it. Every step of the app works without
 * it, offline or without a key; it calls a provider only after the person set one up and pressed send.
 *
 * Simple-view rules: text never below 13px (text-xs), exactly one primary button (its label follows the state: send,
 * confirm a write, or send the one image), every other control is a secondary workspace-button. */
import React, { useEffect, useRef, useState } from 'react';
import { useAssistantStore, assistantBridge } from '../../stores/useAssistantStore';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { AssistantProviderSetup } from './AssistantProviderSetup';
import { ChatgptConnectionSetup } from './ChatgptConnectionSetup';
import { AssistantRunLog } from './AssistantRunLog';

const ITEM_STYLE: Record<string, string> = {
  person: 'ml-8 rounded bg-slate-800 px-3 py-2 text-slate-100',
  assistant: 'mr-4 whitespace-pre-wrap rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100',
  tool: 'text-xs text-slate-300',
  notice: 'rounded border border-slate-600 px-3 py-2 text-xs text-slate-300',
  error: 'rounded border border-rose-700 bg-rose-950/40 px-3 py-2 text-rose-100',
};

export const AssistantPanel: React.FC = () => {
  const isOpen = useAssistantStore(state => state.isOpen);
  const config = useAssistantStore(state => state.config);
  const items = useAssistantStore(state => state.items);
  const busy = useAssistantStore(state => state.busy);
  const pending = useAssistantStore(state => state.pending);
  const imageOffer = useAssistantStore(state => state.imageOffer);
  const hasImage = useAnnotationStore(state => Boolean(state.currentImage));
  const { close, ask, stop, decide, offerImage, sendImage, dropImage, newConversation } = useAssistantStore.getState();
  const [draft, setDraft] = useState('');
  const list = useRef<HTMLOListElement>(null);
  useEffect(() => { list.current?.lastElementChild?.scrollIntoView({ block: 'nearest' }); }, [items.length, pending]);
  if (!isOpen) return null;

  const bridge = assistantBridge();
  const active = config?.providers.find(entry => entry.provider === config.active) ?? null;
  const local = Boolean(active && /^http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/.test(active.baseUrl));
  const chatgpt = active?.provider === 'chatgpt';
  const account = config?.chatgpt?.accounts.find(item => item.recordId === config.chatgpt?.active);
  const ready = Boolean(bridge && active && (chatgpt
    ? active.ready && account?.signedIn && account.planUsageEnabled && !account.usagePaused && account.model && account.model === active.model
    : active.model && (active.hasKey || local)));
  const submit = () => { const text = draft.trim(); if (!text) return; setDraft(''); void ask(text); };
  const primary = pending ? { label: '확인하고 실행', run: () => void decide(true), disabled: false }
    : imageOffer ? { label: '이미지 1장 보내기', run: () => void sendImage(), disabled: false }
    : { label: busy ? '보내는 중…' : '보내기', run: submit, disabled: busy || !ready || !draft.trim() };

  return (
    <aside aria-label="도우미" className="fixed right-0 top-0 z-40 flex h-full w-[26rem] max-w-full flex-col border-l border-slate-700 bg-[#0F141C] text-sm text-slate-100 shadow-2xl">
      <header className="flex items-center justify-between gap-2 border-b border-slate-700 px-4 py-3">
        <h2 className="text-base font-semibold">도우미{active ? <span className="ml-2 text-xs font-normal text-slate-400">{active.label} · {active.model}</span> : null}</h2>
        <button type="button" className="workspace-button" onClick={close}>닫기</button>
      </header>
      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
        <p className="text-xs text-slate-300">도우미 없이도 모든 단계를 쓸 수 있습니다. 승인·배포·정답 확정은 사람이 직접 하고, 쓰기 도구는 여기서 확인해야 실행됩니다.</p>
        {!bridge && <p role="alert" className="text-amber-200">이 화면(브라우저)에서는 도우미를 쓸 수 없습니다. 데스크톱 앱에서 여세요.</p>}
        {bridge && <ChatgptConnectionSetup />}
        {bridge && (
          <details open={!ready&&!(chatgpt&&account?.usagePaused)} className="rounded border border-slate-700 p-2">
            <summary className="cursor-pointer text-slate-200">API 키 공급자 설정 · {!chatgpt && ready ? `${active!.label} 사용 중` : '선택 설정'}</summary>
            <div className="mt-2"><AssistantProviderSetup /></div>
          </details>
        )}
        <ol ref={list} role="log" aria-live="polite" aria-label="도우미 대화" className="space-y-2">
          {items.map(item => (
            <li key={item.id} className={ITEM_STYLE[item.kind]}>
              {item.kind === 'tool' ? `${item.ok ? '✓' : '·'} ${item.text}` : item.text}
              {item.detail && item.kind === 'error' && <span className="mt-1 block text-xs text-rose-200">{item.detail}</span>}
            </li>
          ))}
        </ol>
        {pending && (
          <section role="group" aria-label="쓰기 실행 확인" className="space-y-2 rounded border border-amber-600 bg-amber-950/40 p-3">
            <h3 className="font-semibold text-amber-100">{pending.preview.title} · 확인이 필요합니다</h3>
            <ul className="list-disc space-y-1 pl-5 text-slate-100">{pending.preview.lines.map(line => <li key={line}>{line}</li>)}</ul>
            <p className="text-xs text-slate-300">되돌리는 방법: {pending.preview.undo}</p>
            <button type="button" className="workspace-button" onClick={() => void decide(false)}>실행하지 않기</button>
          </section>
        )}
        {imageOffer && (
          <section role="group" aria-label="이미지 보내기 확인" className="space-y-2 rounded border border-sky-700 bg-sky-950/40 p-3">
            <p className="text-sky-100">{imageOffer.message}</p>
            <p className="text-xs text-slate-300">질문: {imageOffer.question}</p>
            <button type="button" className="workspace-button" onClick={dropImage}>보내지 않기</button>
          </section>
        )}
        {bridge && <AssistantRunLog />}
      </div>
      <footer className="space-y-2 border-t border-slate-700 px-4 py-3">
        {chatgpt && !account?.usagePaused && items.some(item => item.kind === 'error' && item.code === 'rate_limited') && <p role="status" className="text-xs text-amber-200">ChatGPT 사용 한도를 확인하세요. <a href="https://chatgpt.com/settings/usage" target="_blank" rel="noopener noreferrer" className="workspace-button inline-block">ChatGPT 사용량 관리</a></p>}
        <textarea aria-label="도우미에게 묻기" rows={3} value={draft} disabled={!ready || Boolean(pending) || Boolean(imageOffer)}
          onChange={event => setDraft(event.target.value)}
          onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (!busy) submit(); } }}
          placeholder={chatgpt&&account?.usagePaused ? 'ChatGPT 사용량을 확인한 뒤 요청 재개를 허용하세요.' : ready ? '예: 왜 학습을 시작할 수 없나요?' : '공급자를 설정하면 물어볼 수 있습니다.'}
          className="w-full resize-none rounded border border-slate-600 bg-slate-900 px-2 py-1.5 text-slate-100" />
        {chatgpt && <p className="text-xs text-slate-300">ChatGPT 연결은 현재 텍스트 질문만 지원합니다. 이미지 전송은 지원하지 않습니다.</p>}
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={`workspace-button${chatgpt&&account?.usagePaused?'':' workspace-button--primary'}`} disabled={primary.disabled} onClick={primary.run}>{primary.label}</button>
          {busy && !pending && <button type="button" className="workspace-button" onClick={() => void stop()}>멈추기</button>}
          {(active?.supportsImages || chatgpt) && (
            <button type="button" className="workspace-button" disabled={chatgpt || busy || !ready || !hasImage || !draft.trim() || Boolean(pending) || Boolean(imageOffer)}
              onClick={() => { const text = draft.trim(); setDraft(''); void offerImage(text); }}>이미지와 함께 묻기</button>
          )}
          <button type="button" className="workspace-button" disabled={busy || Boolean(pending) || !items.length} onClick={newConversation}>새 대화</button>
        </div>
      </footer>
    </aside>
  );
};
