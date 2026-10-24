/** Optional, explicit ChatGPT account connection. No authorization URL or token enters the renderer. */
import React, { useEffect, useRef, useState } from 'react';
import { assistantBridge, useAssistantStore } from '../../stores/useAssistantStore';

const FIELD = 'w-full rounded border border-slate-600 bg-slate-900 px-2 py-1.5 text-slate-100';
const USAGE = 'https://chatgpt.com/settings/usage';
const TERMS = 'https://openai.com/policies/sign-in-with-chatgpt-terms/';

export const ChatgptConnectionSetup: React.FC = () => {
  const config = useAssistantStore(state => state.config);
  const configError = useAssistantStore(state => state.configError);
  const isOpen = useAssistantStore(state => state.isOpen);
  const chat = config?.chatgpt;
  const account = chat?.accounts.find(item => item.recordId === chat.active);
  const supported = Boolean(assistantBridge()?.chatgpt);
  const pending = chat?.status === 'waiting' || chat?.status === 'exchanging';
  const ready = Boolean(config?.active === 'chatgpt' && account?.signedIn && account.planUsageEnabled && account.model);
  const renderedSelection = JSON.stringify([chat?.active, chat?.authority]);
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [signOutNotice, setSignOutNotice] = useState('');
  const [models, setModels] = useState<{ id: string; label: string }[]>([]);
  const [usageRead, setUsageRead] = useState(false);
  const alive = useRef(true), operation = useRef(0);
  const selection = () => {
    const current = useAssistantStore.getState().config?.chatgpt;
    return JSON.stringify([current?.active, current?.authority]);
  };

  useEffect(() => { alive.current = true; return () => { alive.current = false; operation.current++; }; }, []);
  useEffect(() => { setModels([]); setNote(''); }, [chat?.active, chat?.authority]);
  useEffect(() => {
    if (!isOpen || !pending || !supported) return;
    let inFlight = false, stopped = false;
    const timer = setInterval(async () => {
      const state = useAssistantStore.getState(), status = state.config?.chatgpt?.status;
      if (stopped || inFlight || !state.isOpen || (status !== 'waiting' && status !== 'exchanging')) return;
      inFlight = true;
      try { await state.loadConfig(); } finally { inFlight = false; }
    }, 1000);
    return () => { stopped = true; clearInterval(timer); };
  }, [isOpen, pending, supported]);

  const run = async (action: () => Promise<boolean>, onSuccess?: () => void) => {
    const id = ++operation.current;
    setBusy(true); setNote(''); setSignOutNotice('');
    try {
      const ok = await action();
      if (alive.current && id === operation.current && ok) onSuccess?.();
    } catch {
      if (alive.current && id === operation.current) setNote('연결 상태를 읽지 못했습니다. 데스크톱 앱의 연결을 확인한 뒤 다시 시도하세요.');
    } finally { if (alive.current && id === operation.current) setBusy(false); }
  };
  const begin = (recordId?: string) => {
    if (!accepted || !supported || !chat?.encryptionAvailable || pending || busy) return Promise.resolve();
    if (recordId && renderedSelection !== selection()) return Promise.resolve();
    return run(() => useAssistantStore.getState().beginChatgpt({ termsAccepted: true, ...(recordId ? { recordId } : {}) }));
  };
  const loadModels = async () => {
    if (!account?.signedIn || !account.planUsageEnabled || busy || pending) return;
    const binding = selection(), id = ++operation.current;
    setBusy(true); setNote('');
    try {
      const result = await useAssistantStore.getState().listModels('chatgpt');
      if (!alive.current || id !== operation.current || binding !== selection()) return;
      if (Array.isArray(result)) {
        setModels(result);
        setNote(result.length ? '사용 가능한 모델을 불러왔습니다. 사용할 모델을 직접 고르세요.' : '이 계정에 표시할 모델이 없습니다. 연결과 플랜 사용 권한을 확인하세요.');
      } else setNote(`${result.reason} ${result.action}`);
    } catch {
      if (alive.current && id === operation.current && binding === selection()) setNote('모델 목록을 읽지 못했습니다. 연결을 확인한 뒤 다시 불러오세요.');
    } finally { if (alive.current && id === operation.current) setBusy(false); }
  };
  const chooseModel = (model: string) => {
    if (!account || busy || pending || renderedSelection !== selection() || !models.some(item => item.id === model)) return Promise.resolve();
    return run(() => useAssistantStore.getState().selectChatgptModel(account.recordId, model));
  };
  const signOut = () => {
    if (!account || busy || renderedSelection !== selection()) return Promise.resolve();
    return run(() => useAssistantStore.getState().signOutChatgpt(account.recordId), () => {
      const confirmed = useAssistantStore.getState().config?.chatgpt?.remoteRevocationConfirmed;
      setSignOutNotice(confirmed ? '이 컴퓨터의 연결을 지웠고 서비스 쪽 연결 해제도 확인했습니다.' : '이 컴퓨터의 연결을 지웠습니다. 서비스 쪽 연결 해제는 확인되지 않았습니다. 필요하면 ChatGPT 설정에서도 연결을 확인하세요.');
    });
  };

  return (
    <section aria-label="ChatGPT 연결" className="space-y-2 rounded border border-slate-700 p-2 text-xs">
      <details open={Boolean(pending || account)}>
        <summary className="cursor-pointer font-semibold text-slate-200">ChatGPT 연결 (선택)</summary>
        <div className="mt-2 space-y-2">
          <p>API 키 대신 본인의 ChatGPT 계정으로 연결할 수 있습니다. 기본 검사·학습 단계는 이 연결 없이 동작합니다.</p>
          <p>로그인 토큰은 이 컴퓨터의 보호 저장소에만 보관합니다. 질문과 필요한 최소 작업 상태만 선택한 계정 범위로 보냅니다. 프로젝트 파일·키·로컬 경로 전체는 보내지 않습니다. 현재는 텍스트 질문만 지원하며 이미지는 보내지 않습니다.</p>
          <p><a className="text-sky-300 underline" href={TERMS} target="_blank" rel="noopener noreferrer">Sign in with ChatGPT 약관</a>을 확인하세요. 로그인은 아래 버튼을 눌러 연 브라우저에서 직접 진행합니다.</p>
          <label className="flex items-start gap-2"><input type="checkbox" checked={accepted} onChange={event => setAccepted(event.target.checked)} />약관과 전송·보관 안내를 읽고 동의합니다.</label>
          {!supported && <p role="status">이 앱에서는 ChatGPT 연결을 사용할 수 없습니다. 최신 데스크톱 앱에서 여세요.</p>}
          {supported && config && !chat?.encryptionAvailable && <p role="alert" className="text-amber-200">보호 저장소를 쓸 수 없어 로그인 연결을 시작하지 않습니다. 이 컴퓨터의 보호 저장소를 켠 뒤 다시 여세요.</p>}
          {Boolean(chat?.accounts.length) && <label className="block">연결 계정
            <select aria-label="ChatGPT 계정" className={FIELD} value={chat?.active ?? ''} disabled={busy || pending} onChange={event => {
              const value = event.target.value;
              if (value && !chat?.accounts.some(item => item.recordId === value)) return;
              void run(() => useAssistantStore.getState().selectChatgptAccount(value || null));
            }}>
              <option value="">계정 선택</option>
              {chat?.accounts.map((item, index) => <option key={item.recordId} value={item.recordId}>{item.label && !item.label.includes('@') ? item.label : `ChatGPT 계정 ${index + 1}`}</option>)}
            </select>
          </label>}
          <div className="flex flex-wrap gap-2">
            <button type="button" className="workspace-button" disabled={!supported || !accepted || !chat?.encryptionAvailable || pending || busy} onClick={() => begin()}>Continue with ChatGPT</button>
            {account && <button type="button" className="workspace-button" disabled={!accepted || !chat?.encryptionAvailable || pending || busy} onClick={() => begin(account.recordId)}>선택 계정 다시 연결</button>}
            {Boolean(chat?.accounts.length) && <span className="self-center text-slate-400">다른 계정은 Continue with ChatGPT에서 추가합니다.</span>}
            {pending && <button type="button" className="workspace-button" onClick={() => run(() => useAssistantStore.getState().cancelChatgpt())}>연결 취소</button>}
            {account && <button type="button" className="workspace-button" disabled={busy || pending} onClick={signOut}>이 컴퓨터에서 연결 지우기</button>}
          </div>
          {pending && <p role="status">{chat?.status === 'exchanging' ? '브라우저에서 승인한 연결을 확인하고 있습니다.' : '브라우저에서 로그인을 마친 뒤 이 화면으로 돌아오세요.'}</p>}
          {account && !account.signedIn && <p role="status">저장된 계정의 로그인이 만료되었습니다. 약관 안내를 확인하고 선택 계정을 다시 연결하세요.</p>}
          {account?.signedIn && !account.planUsageEnabled && <p role="status">로그인은 확인했지만 ChatGPT 플랜 사용은 활성화되지 않았습니다. 질문을 보내지 않습니다. 지원 범위와 계정 권한을 확인하세요.</p>}
          {account && <>
            <button type="button" className="workspace-button" disabled={busy || pending || !account.signedIn || !account.planUsageEnabled} onClick={loadModels}>모델 목록 불러오기</button>
            <label className="block">사용할 모델
              <select aria-label="ChatGPT 모델" className={FIELD} value={account.model || ''} disabled={busy || pending || !account.signedIn || !account.planUsageEnabled || !models.length} onChange={event => chooseModel(event.target.value)}>
                <option value="">목록을 불러온 뒤 모델 선택</option>
                {account.model && !models.some(item => item.id === account.model) && <option value={account.model}>{account.model} (선택됨)</option>}
                {models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
              </select>
            </label>
          </>}
        </div>
      </details>
      {ready && <>
        <p className="text-emerald-200">ChatGPT 플랜 사용 중 · <a href={USAGE} target="_blank" rel="noopener noreferrer" className="underline">사용량 관리</a></p>
        {!usageRead && <div role="status" className="space-y-1 rounded border border-sky-700 p-2"><p>이 도우미의 질문은 선택한 계정의 ChatGPT 플랜 사용량을 사용합니다. 전송 전 계정과 모델을 확인하세요.</p><button type="button" className="workspace-button" onClick={() => setUsageRead(true)}>안내 확인</button></div>}
      </>}
      {chat?.error && <div role="alert" className="space-y-1 text-amber-200"><p>{chat.error.reason} {chat.error.action}</p>{chat.error.code === 'rate_limited' && <a href={USAGE} target="_blank" rel="noopener noreferrer" className="workspace-button inline-block">ChatGPT 사용량 관리</a>}</div>}
      {(note || configError || signOutNotice) && <p role="status" className="text-slate-300">{configError || signOutNotice || note}</p>}
    </section>
  );
};
