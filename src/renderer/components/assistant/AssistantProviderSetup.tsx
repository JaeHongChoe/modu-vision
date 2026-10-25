/** Provider setup for the built-in assistant: which provider, base URL, request format, model, whether the model takes
 * images, and the key. The key field sends its value straight to the main process and clears itself; the panel only
 * ever learns "키 저장됨". */
import React, { useEffect, useState } from 'react';
import type { AssistantApiFormat, AssistantApiKeyProviderId } from '../../../types/electron';
import { useAssistantStore } from '../../stores/useAssistantStore';

const DEFAULT_BASE: Record<AssistantApiKeyProviderId, string> = { 'openai-compatible': 'https://api.openai.com/v1', anthropic: 'https://api.anthropic.com' };
const FIELD = 'w-full rounded border border-slate-600 bg-slate-900 px-2 py-1.5 text-slate-100';

export const AssistantProviderSetup: React.FC = () => {
  const config = useAssistantStore(state => state.config);
  const configError = useAssistantStore(state => state.configError);
  const { saveProvider, removeKey, setActive, listModels } = useAssistantStore.getState();
  const [provider, setProvider] = useState<AssistantApiKeyProviderId>(config?.active === 'anthropic' ? 'anthropic' : 'openai-compatible');
  const saved = config?.providers.find(entry => entry.provider === provider) ?? null;
  const [baseUrl, setBaseUrl] = useState(saved?.baseUrl ?? DEFAULT_BASE[provider]);
  const [model, setModel] = useState(saved?.model ?? '');
  const [format, setFormat] = useState<AssistantApiFormat>(saved?.format ?? 'chat');
  const [images, setImages] = useState(saved?.supportsImages ?? false);
  const [keyDraft, setKeyDraft] = useState('');
  const [models, setModels] = useState<{ id: string; label: string }[]>([]);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);

  // Switching provider (or the first read of the settings) shows that provider's saved settings or its defaults.
  const loaded = Boolean(config);
  useEffect(() => { const active = useAssistantStore.getState().config?.active; if (active === 'anthropic' || active === 'openai-compatible') setProvider(active); }, [loaded]);
  useEffect(() => {
    const entry = useAssistantStore.getState().config?.providers.find(item => item.provider === provider);
    setBaseUrl(entry?.baseUrl ?? DEFAULT_BASE[provider]);setModel(entry?.model ?? '');
    setFormat(entry?.format ?? 'chat');setImages(entry?.supportsImages ?? false);setModels([]);setNote('');
  }, [provider, loaded]);

  const save = async () => {
    setBusy(true);setNote('');
    const key = keyDraft;
    setKeyDraft('');  // the field never keeps the key, whatever the outcome
    try {
      const done = await saveProvider({ provider, baseUrl, model, ...(provider === 'openai-compatible' ? { format } : {}), supportsImages: images, ...(key ? { key } : {}) });
      if (done) setNote(key ? '설정과 키를 저장했습니다. 키는 이 컴퓨터의 보호 저장소에만 있습니다.' : '설정을 저장했습니다.');
    } finally { setBusy(false); }
  };
  const loadModels = async () => {
    setBusy(true);setNote('');
    try {
      const result = await listModels(provider);
      if (Array.isArray(result)) { setModels(result);setNote(result.length ? `모델 ${result.length}개를 불러왔습니다. 목록에서 고르거나 직접 입력하세요.` : '공급자가 모델 목록을 주지 않았습니다. 모델 ID를 직접 입력하세요.'); }
      else setNote(`${result.reason} ${result.action}`);
    } finally { setBusy(false); }
  };

  return (
    <form className="space-y-2" onSubmit={event => { event.preventDefault(); void save(); }}>
      {config && !config.encryptionAvailable && (
        <p role="alert" className="rounded border border-amber-700 bg-amber-950/40 p-2 text-amber-100">
          이 컴퓨터의 보호 저장소(키체인 등)를 쓸 수 없어 키를 저장하지 않습니다. OS의 키 저장소를 켠 뒤 다시 여세요. 키가 필요 없는 이 컴퓨터의 로컬 서버는 쓸 수 있습니다.
        </p>
      )}
      <label className="block">공급자
        <select aria-label="도우미 공급자" className={FIELD} value={provider} onChange={event => setProvider(event.target.value as AssistantApiKeyProviderId)}>
          <option value="openai-compatible">OpenAI 호환 (OpenAI, 로컬 서버 등)</option>
          <option value="anthropic">Anthropic</option>
        </select>
      </label>
      <label className="block">기본 주소
        <input aria-label="공급자 기본 주소" className={FIELD} value={baseUrl} onChange={event => setBaseUrl(event.target.value)} spellCheck={false} autoComplete="off" />
      </label>
      {provider === 'openai-compatible' && (
        <label className="block">요청 형식
          <select aria-label="요청 형식" className={FIELD} value={format} onChange={event => setFormat(event.target.value as AssistantApiFormat)}>
            <option value="chat">Chat Completions (대부분의 호환 서버)</option>
            <option value="responses">Responses (OpenAI 최신 모델)</option>
          </select>
        </label>
      )}
      <label className="block">모델
        <input aria-label="모델 이름" list="assistant-model-list" className={FIELD} value={model} onChange={event => setModel(event.target.value)} spellCheck={false} autoComplete="off" placeholder="모델 ID" />
        <datalist id="assistant-model-list">{models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</datalist>
      </label>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={images} onChange={event => setImages(event.target.checked)} />
        이 모델은 이미지를 받습니다(이미지와 함께 묻기를 켭니다)
      </label>
      <label className="block">키 {saved?.hasKey ? <span className="text-emerald-300">· 키 저장됨</span> : <span className="text-slate-400">· 저장된 키 없음</span>}
        <input aria-label="공급자 키" type="password" className={FIELD} value={keyDraft} onChange={event => setKeyDraft(event.target.value)}
          autoComplete="off" spellCheck={false} placeholder={saved?.hasKey ? '바꿀 때만 새 키 입력' : '공급자 콘솔에서 만든 키'} />
      </label>
      <p className="text-xs text-slate-400">요금은 키 주인에게 청구됩니다. 키는 이 컴퓨터의 보호 저장소에만 두고 화면·기록·프로젝트에는 남기지 않습니다.</p>
      <div className="flex flex-wrap gap-2">
        <button type="submit" className="workspace-button" disabled={busy}>설정 저장</button>
        <button type="button" className="workspace-button" disabled={busy || !saved} onClick={() => void loadModels()}>모델 목록 불러오기</button>
        {saved?.hasKey && <button type="button" className="workspace-button" disabled={busy} onClick={() => void removeKey(provider)}>키 지우기</button>}
        {saved && config?.active !== provider && <button type="button" className="workspace-button" disabled={busy} onClick={() => void setActive(provider)}>이 공급자 쓰기</button>}
      </div>
      {(note || configError) && <p role="status" className="text-xs text-slate-300">{configError || note}</p>}
    </form>
  );
};
