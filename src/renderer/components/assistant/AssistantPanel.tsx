/** The built-in assistant (docs/product-direction-easy-labeling-agent.md §4): a side panel that explains where the
 * person is stuck, opens the right screen, and runs the app's own tools — writes only after the person reads the preview
 * and confirms here. Mount it once; `useAssistantStore.getState().open()` shows it. Every step of the app works without
 * it, offline or without an account; this panel sends only through an explicitly connected ChatGPT account.
 *
 * Simple-view rules: text never below 13px (text-xs), one primary send/confirm button;
 * all other controls are secondary workspace-buttons. */
import React, { useEffect, useRef, useState } from 'react';
import { useAssistantStore, assistantBridge } from '../../stores/useAssistantStore';
import { ChatgptConnectionSetup } from './ChatgptConnectionSetup';
import { AssistantRunLog } from './AssistantRunLog';
import {AssistantTaskPanel} from './AssistantTaskPanel';
import {useAssistantTaskStore} from '../../stores/useAssistantTaskStore';
import {taskConnectionKey} from '../../services/assistantTasks/connection';

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
  const writing = useAssistantStore(state => state.writing);
  const configuring = useAssistantStore(state => state.configuring);
  const configReadStatus = useAssistantStore(state => state.configReadStatus);
  const configError = useAssistantStore(state => state.configError);
  const pendingWrite = useAssistantStore(state => state.pending);
  const taskStatus=useAssistantTaskStore(state=>state.task?.status);
  const taskBusy=useAssistantTaskStore(state=>state.busy);
  const planningNew=useAssistantTaskStore(state=>state.planningNew);
  const { close, ask, stop, decide, newConversation, stopConfigLoad, loadConfig } = useAssistantStore.getState();
  const [draft, setDraft] = useState('');
  const list = useRef<HTMLOListElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { list.current?.lastElementChild?.scrollIntoView({ block: 'nearest' }); }, [items.length, pendingWrite]);
  if (!isOpen) return null;

  const bridge = assistantBridge();
  const active = config?.active === 'chatgpt' ? config.providers.find(entry => entry.provider === 'chatgpt') ?? null : null;
  const chatgpt = active?.provider === 'chatgpt';
  const account = config?.chatgpt?.accounts.find(item => item.recordId === config.chatgpt?.active);
  const ready = Boolean(configReadStatus !== 'unverified' && bridge && active?.ready && account?.signedIn && account.planUsageEnabled && !account.usagePaused && account.model && account.model === active.model);
  const pending = chatgpt ? pendingWrite : null;
  // Retained handlers must not continue a former connection after the settings change.
  const connectionCurrent = () => { const now = useAssistantStore.getState(); return ready && now.config === config && now.config?.active === 'chatgpt' && !now.configuring && now.configReadStatus !== 'unverified'; };
  // The typed question is cleared only when it is actually handed over; while settings load it stays in the composer.
  const canHandOver = () => { const now = useAssistantStore.getState(); return connectionCurrent() && !now.busy && !now.pending && !now.writing; };
  const submit = () => { const text = draft.trim(); if (!text || !canHandOver()) return; setDraft(''); void ask(text); };
  const preparedNewRequest = () => {
    if(!canHandOver()||useAssistantStore.getState().items!==items||!useAssistantTaskStore.getState().planningNew)return;
    newConversation();
    composer.current?.focus();
  };
  const confirm = (accepted: boolean) => { const now = useAssistantStore.getState(); if (!pending || now.pending !== pending || !connectionCurrent() || now.writing) return; void decide(accepted); };
  const primary = pending ? { label: '확인하고 실행', run: () => confirm(true), disabled: configuring || writing || !ready }
    : { label: writing ? '실행 중…' : configuring ? '설정 확인 중…' : busy ? '보내는 중…' : '보내기', run: submit, disabled: busy || configuring || !ready || !draft.trim() };

  return (
    <aside aria-label="도우미" className="fixed right-0 top-0 z-40 flex h-full w-[26rem] max-w-full flex-col border-l border-slate-700 bg-[#0F141C] text-sm text-slate-100 shadow-2xl">
      <header className="flex items-center justify-between gap-2 border-b border-slate-700 px-4 py-3">
        <h2 className="text-base font-semibold">ChatGPT 도우미{active?.model ? <span className="ml-2 text-xs font-normal text-slate-400">{active.model}</span> : null}</h2>
        <button type="button" className="workspace-button" onClick={close}>닫기</button>
      </header>
      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
        <p className="text-xs text-slate-300">도우미 없이도 모든 단계를 쓸 수 있습니다. 작업 계획을 확인하면 도우미가 화면을 조작하며 진행합니다. 승인·배포·정답 확정은 사람이 직접 합니다.</p>
        {!bridge && <p role="alert" className="text-amber-200">이 화면(브라우저)에서는 도우미를 쓸 수 없습니다. 데스크톱 앱에서 여세요.</p>}
        {bridge && (configReadStatus === 'waiting' || configReadStatus === 'unverified') && (
          <section aria-label="연결 상태 확인" className="workspace-section space-y-2">
            {configReadStatus === 'waiting'
              ? <p role="status" aria-busy="true">연결 상태를 확인하는 중입니다.</p>
              : <p role="alert" className="text-[var(--workspace-warning)]">{configError || '연결 상태가 아직 확인되지 않았습니다.'}</p>}
            <p className="text-xs">운영체제의 보호 요청이 있다면 확인한 뒤 다시 확인하세요. 대기 중지는 화면의 기다림만 끝내며 운영체제 작업을 취소하지 않습니다.</p>
            {configReadStatus === 'waiting'
              ? <button type="button" className="workspace-button" onClick={stopConfigLoad}>설정 확인 대기 중지</button>
              : <button type="button" className="workspace-button" onClick={() => void loadConfig({ explicit: true })}>연결 상태 다시 확인</button>}
          </section>
        )}
        {bridge && (
          // Locked while a confirmed write executes: a receiver change then would end the turn before its outcome is recorded.
          <fieldset aria-label="도우미 설정" disabled={Boolean(writing||taskBusy)} className="min-w-0 space-y-3">
            <ChatgptConnectionSetup />
          </fieldset>
        )}
        <ol ref={list} role="log" aria-live="polite" aria-label="도우미 대화" className="space-y-2">
          {items.map(item => (
            <li key={item.id} className={ITEM_STYLE[item.kind]}>
              {item.kind === 'tool' ? `${item.ok ? '✓' : '·'} ${item.text}` : item.text}
              {item.detail && item.kind === 'error' && <span className="mt-1 block text-xs text-rose-200">{item.detail}</span>}
            </li>
          ))}
        </ol>
        {bridge&&<AssistantTaskPanel ready={ready&&!configuring} blocked={Boolean(busy||writing||pending)} connectionKey={taskConnectionKey(config)} onNewRequestPrepared={preparedNewRequest}/>}
        {pending && (
          <section role="group" aria-label="쓰기 실행 확인" className="space-y-2 rounded border border-amber-600 bg-amber-950/40 p-3">
            <h3 className="font-semibold text-amber-100">{pending.preview.title} · 확인이 필요합니다</h3>
            <ul className="list-disc space-y-1 pl-5 text-slate-100">{pending.preview.lines.map(line => <li key={line}>{line}</li>)}</ul>
            <p className="text-xs text-slate-300">되돌리는 방법: {pending.preview.undo}</p>
            <button type="button" className="workspace-button" disabled={configuring || writing || !ready} onClick={() => confirm(false)}>실행하지 않기</button>
          </section>
        )}
        {bridge && <AssistantRunLog />}
      </div>
      <footer className="space-y-2 border-t border-slate-700 px-4 py-3">
        {chatgpt && !account?.usagePaused && items.some(item => item.kind === 'error' && item.code === 'rate_limited') && <p role="status" className="text-xs text-amber-200">ChatGPT 사용 한도를 확인하세요. <a href="https://chatgpt.com/settings/usage" target="_blank" rel="noopener noreferrer" className="workspace-button inline-block">ChatGPT 사용량 관리</a></p>}
        <textarea ref={composer} aria-label="도우미에게 묻기" rows={3} value={draft} disabled={!ready || Boolean(pending)}
          onChange={event => setDraft(event.target.value)}
          onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (!busy) submit(); } }}
          placeholder={chatgpt&&account?.usagePaused ? 'ChatGPT 사용량을 확인한 뒤 요청 재개를 허용하세요.' : ready ? '예: 이 자료로 불량 검사 만들어줘' : 'ChatGPT 계정을 연결하고 모델을 선택하면 물어볼 수 있습니다.'}
          className="w-full resize-none rounded border border-slate-600 bg-slate-900 px-2 py-1.5 text-slate-100" />
        <p className="text-xs text-slate-300">ChatGPT 연결은 현재 텍스트 질문만 지원합니다. 이미지 전송은 지원하지 않습니다.</p>
        <p className="text-xs text-slate-300">대화만 비워도 저장된 계획은 남습니다. 다른 작업을 시작하려면 계획의 ‘이 계획 멈추고 새 요청’을 선택하세요.</p>
        {writing && <p role="status" className="text-xs text-amber-200">확인한 작업을 실행 중입니다. 결과가 대화와 실행 기록에 남으면 다시 묻거나 설정을 바꿀 수 있습니다.</p>}
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={`workspace-button${chatgpt&&account?.usagePaused||taskStatus&&!planningNew?'':' workspace-button--primary'}`} disabled={primary.disabled} onClick={primary.run}>{primary.label}</button>
          {busy && !pending && !writing && <button type="button" className="workspace-button" onClick={() => void stop()}>멈추기</button>}
          <button type="button" className="workspace-button" disabled={busy || Boolean(pending) || !items.length} onClick={newConversation}>대화만 비우기</button>
        </div>
      </footer>
    </aside>
  );
};
