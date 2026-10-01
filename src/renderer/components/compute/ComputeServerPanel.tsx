import { ComputeReservations } from './ComputeReservations';
import {ComputeJobsPanel} from './ComputeJobsPanel';
import {SharedProjectPanel} from './SharedProjectPanel';
import {ServerConnectionWizard} from './ServerConnectionWizard';
import React, { useState,useEffect } from 'react';
import { CheckCircle2, Pencil, Plus, RefreshCw, Server, Trash2, X } from 'lucide-react';
import { useComputeStore } from '../../stores/useComputeStore';
import type { ComputeProfile, ComputeProfileInput, ComputeProbeResult } from '../../services/api';

interface Props {
  onClose: () => void;
}

const emptyProfile: ComputeProfileInput = {
  name: '',
  ssh_target: '',
  ssh_port: 22,
  remote_root: '',
  runtime_kind: 'python',
  runtime_value: '',
  gpu_selector: '',
};

function probeChecks(result: ComputeProbeResult): Array<[string, string]> {
  if (!result.checks) return [];
  if (Array.isArray(result.checks)) {
    return result.checks.map((value, index) => [String(index + 1), typeof value === 'object' ? JSON.stringify(value) : String(value)]);
  }
  return Object.entries(result.checks).map(([key, value]) => {
    if (key === 'runtime_dependencies' && value && typeof value === 'object' && !Array.isArray(value)) {
      const checks = Object.values(value);
      return ['필수 패키지', `${checks.filter(Boolean).length}/${checks.length} 확인`];
    }
    if (key === 'pretrained_weights' && value && typeof value === 'object' && !Array.isArray(value)) {
      const weights = Object.values(value);
      const verified = weights.filter((item) => item && typeof item === 'object' && 'ok' in item && item.ok).length;
      return ['모델 가중치', `${verified}/${weights.length} 해시 검증`];
    }
    if (key === 'free_bytes' && typeof value === 'number') {
      return ['사용 가능 공간', `${(value / 1_000_000_000).toFixed(1)} GB`];
    }
    if (typeof value === 'boolean') return [key, value ? '통과' : '실패'];
    return [key, typeof value === 'object' ? JSON.stringify(value) : String(value)];
  });
}

export const ComputeServerPanel: React.FC<Props> = ({ onClose }) => {
  const {
    profiles, selectedProfileId, isLoaded, isLoading, isSaving, probePendingId,
    loadError, error, probeResults, load, saveProfile, deleteProfile, selectTarget, probeProfile,
  } = useComputeStore();
  const transportRevision=useComputeStore(state=>state.transportRevision);
  const [draft, setDraft] = useState<ComputeProfileInput>(emptyProfile);
  const [showForm, setShowForm] = useState(false);

  useEffect(()=>{setDraft(emptyProfile);setShowForm(false);},[transportRevision]);

  const edit = (profile: ComputeProfile) => {
    setDraft({ ...profile });
    setShowForm(true);
  };

  const save = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    try {
      await saveProfile({
        ...draft,
        name: draft.name.trim(),
        ssh_target: draft.ssh_target.trim(),
        remote_root: draft.remote_root.trim(),
        runtime_value: draft.runtime_value.trim(),
        gpu_selector: draft.gpu_selector?.trim() || null,
      });
      setDraft(emptyProfile);
      setShowForm(false);
    } catch {
      // The store keeps the validation or network error visible in this panel.
    }
  };

  const remove = async (profile: ComputeProfile) => {
    if (!window.confirm(`서버 설정 “${profile.name}”을 삭제할까요?`)) return;
    try { await deleteProfile(profile.id); } catch { /* Store error is shown below. */ }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-end bg-black/60 p-4" role="presentation" onClick={onClose}>
      <section
        role="dialog"
        aria-modal="true"
        aria-label="컴퓨팅 서버 관리"
        className="w-full max-w-[480px] max-h-[calc(100vh-2rem)] overflow-y-auto rounded border border-[#364357] bg-[#101722] p-4 shadow-2xl text-slate-200"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 border-b border-[#2B3547] pb-3">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-bold"><Server className="h-4 w-4 text-blue-400" /> 컴퓨팅 서버 관리</h2>
            <p className="mt-1 text-xs text-slate-400">SSH 연결과 실행 환경을 저장하고 학습 전에 연결 검사를 실행하세요.</p>
          </div>
          <button type="button" aria-label="닫기" onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-[#263246] hover:text-white"><X className="h-4 w-4" /></button>
        </div>

        {(loadError || error) && (
          <div role="alert" className="mt-3 rounded border border-red-700 bg-red-950/40 px-3 py-2 text-xs text-red-200">
            {loadError || error}
          </div>
        )}
        {!isLoaded && (
          <button type="button" onClick={() => void load().catch(() => {})} disabled={isLoading}
            className="mt-3 inline-flex items-center gap-2 rounded border border-blue-600 px-3 py-1.5 text-xs text-blue-300 disabled:opacity-50">
            <RefreshCw className="h-3.5 w-3.5" /> {isLoading ? '서버 목록 확인 중...' : '서버 목록 다시 불러오기'}
          </button>
        )}

        <SharedProjectPanel />
        <ServerConnectionWizard onAdd={()=>{setDraft(emptyProfile);setShowForm(true);}} onEdit={edit}/>
        <ComputeReservations />
        <div className="mt-4 flex items-center justify-between">
          <h3 className="text-[11px] font-bold uppercase tracking-wide text-slate-400">저장된 서버</h3>
          <button type="button" onClick={() => { setDraft(emptyProfile); setShowForm(true); }}
            className="inline-flex items-center gap-1 rounded border border-blue-600 px-2 py-1 text-xs text-blue-300 hover:bg-blue-950/40">
            <Plus className="h-3.5 w-3.5" /> 서버 추가
          </button>
        </div>
        {profiles.length === 0 && <p className="mt-3 rounded border border-[#2B3547] p-3 text-xs text-slate-400">저장된 서버가 없습니다.</p>}
        <div className="mt-2 space-y-2">
          {profiles.map((profile) => {
            const result = probeResults[profile.id];
            const selected = selectedProfileId === profile.id;
            return (
              <div key={profile.id} className={`rounded border p-3 ${selected ? 'border-blue-500 bg-blue-950/20' : 'border-[#2B3547] bg-[#141C29]'}`}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-xs font-semibold text-slate-100">
                      <span className="truncate">{profile.name}</span>
                      {selected && <span className="rounded bg-blue-900/60 px-1.5 py-0.5 text-[10px] text-blue-200">새 작업 대상</span>}
                    </div>
                    <div className="mt-1 break-all font-mono text-[11px] text-slate-400">{profile.ssh_target}:{profile.ssh_port} · {profile.runtime_kind}</div>
                    <div className="mt-1 break-all font-mono text-[10px] text-slate-500">{profile.remote_root}</div>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <button type="button" aria-label={`${profile.name} 편집`} title="편집" onClick={() => edit(profile)} className="rounded p-1.5 text-slate-400 hover:bg-[#263246] hover:text-white"><Pencil className="h-3.5 w-3.5" /></button>
                    <button type="button" aria-label={`${profile.name} 삭제`} title="삭제" onClick={() => void remove(profile)} className="rounded p-1.5 text-slate-400 hover:bg-red-950/40 hover:text-red-300"><Trash2 className="h-3.5 w-3.5" /></button>
                  </div>
                </div>
                <div className="mt-3 flex items-center gap-2">
                  {!selected && <button type="button" onClick={() => void selectTarget(profile.id).catch(() => {})} className="rounded border border-blue-700 px-2 py-1 text-[11px] text-blue-300">이 서버 사용</button>}
                  <button type="button" onClick={() => void probeProfile(profile.id).catch(() => {})} disabled={probePendingId === profile.id}
                    className="inline-flex items-center gap-1 rounded border border-[#4B5D77] px-2 py-1 text-[11px] text-slate-200 disabled:opacity-50">
                    <RefreshCw className="h-3 w-3" /> {probePendingId === profile.id ? '검사 중...' : '연결 검사'}
                  </button>
                  {result && <span className={`text-[11px] font-semibold ${result.ready ? 'text-emerald-400' : 'text-amber-300'}`}>
                    {result.ready ? '준비 완료' : '사용 불가'}
                  </span>}
                </div>
                {result && (
                  <div className="mt-2 rounded bg-[#0B0E14] p-2 text-[11px] text-slate-300">
                    <div className="flex items-center gap-1">
                      {result.ready && <CheckCircle2 className="h-3 w-3 text-emerald-400" />}
                      <span>{result.device_name || result.device_type || result.message || '장치 정보 없음'}</span>
                    </div>
                    {result.message && result.device_name && <div className="mt-1 text-slate-400">{result.message}</div>}
                    {probeChecks(result).map(([name, value]) => <div key={name} className="mt-1 flex justify-between gap-2 font-mono text-slate-500"><span>{name}</span><span className="text-right">{value}</span></div>)}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {showForm && (
          <form onSubmit={(event) => void save(event)} className="mt-4 space-y-2 rounded border border-[#364357] bg-[#0B0E14] p-3">
            <div className="flex items-center justify-between text-xs font-bold">
              <span>{draft.id ? '서버 설정 편집' : '서버 추가'}</span>
              <button type="button" onClick={() => setShowForm(false)} className="text-slate-400 hover:text-white">취소</button>
            </div>
            <label className="block text-[11px] text-slate-400">표시 이름<input required value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} className="mt-1 w-full rounded border border-[#364357] bg-[#131822] px-2 py-1.5 text-xs text-white" /></label>
            <div className="grid grid-cols-[1fr_90px] gap-2">
              <label className="block text-[11px] text-slate-400">SSH 호스트 또는 별칭<input required value={draft.ssh_target} onChange={(event) => setDraft({ ...draft, ssh_target: event.target.value })} className="mt-1 w-full rounded border border-[#364357] bg-[#131822] px-2 py-1.5 text-xs text-white" /></label>
              <label className="block text-[11px] text-slate-400">SSH 포트<input required type="number" min="1" max="65535" value={draft.ssh_port} onChange={(event) => setDraft({ ...draft, ssh_port: Number(event.target.value) })} className="mt-1 w-full rounded border border-[#364357] bg-[#131822] px-2 py-1.5 text-xs text-white" /></label>
            </div>
            <label className="block text-[11px] text-slate-400">원격 작업 폴더<input required pattern="/.*" title="절대 경로를 입력하세요" value={draft.remote_root} onChange={(event) => setDraft({ ...draft, remote_root: event.target.value })} placeholder="/data/home/user/modu-vision" className="mt-1 w-full rounded border border-[#364357] bg-[#131822] px-2 py-1.5 text-xs text-white" /></label>
            <div className="grid grid-cols-[100px_1fr] gap-2">
              <label className="block text-[11px] text-slate-400">실행 방식<select value={draft.runtime_kind} onChange={(event) => setDraft({ ...draft, runtime_kind: event.target.value as 'python' | 'docker' })} className="mt-1 w-full rounded border border-[#364357] bg-[#131822] px-2 py-1.5 text-xs text-white"><option value="python">Python</option><option value="docker">Docker</option></select></label>
              <label className="block text-[11px] text-slate-400">{draft.runtime_kind === 'python' ? 'Python 실행 파일' : 'Docker 이미지'}<input required value={draft.runtime_value} onChange={(event) => setDraft({ ...draft, runtime_value: event.target.value })} placeholder={draft.runtime_kind === 'python' ? '/usr/bin/python3' : 'image:tag'} className="mt-1 w-full rounded border border-[#364357] bg-[#131822] px-2 py-1.5 text-xs text-white" /></label>
            </div>
            <label className="block text-[11px] text-slate-400">GPU 선택자 (비우면 CPU)<input value={draft.gpu_selector || ''} onChange={(event) => setDraft({ ...draft, gpu_selector: event.target.value })} placeholder="0,1 · GPU UUID · MIG UUID · all" className="mt-1 w-full rounded border border-[#364357] bg-[#131822] px-2 py-1.5 text-xs text-white" /></label>
            <label className="flex items-center gap-2 text-[11px] text-slate-300"><input type="checkbox" checked={!!draft.allow_sharing} onChange={event=>setDraft({...draft,allow_sharing:event.target.checked,distributed_processes:1})}/>이 GPU에서 메모리 예산 안의 여러 작업 허용</label>
            <div className="grid grid-cols-2 gap-2"><label className="text-[11px] text-slate-400">작업당 메모리 (MiB)<input aria-label="작업당 GPU 메모리 예산" type="number" min={1} max={1048576} value={draft.memory_budget_mb||''} onChange={event=>setDraft({...draft,memory_budget_mb:event.target.value?Number(event.target.value):null})} className="mt-1 w-full rounded border border-[#364357] bg-[#131822] px-2 py-1.5 text-xs text-white"/></label><label className="text-[11px] text-slate-400">단일 모델 분산 GPU 수<input aria-label="단일 모델 분산 GPU 수" type="number" min={1} max={16} disabled={!!draft.allow_sharing} value={draft.distributed_processes||1} onChange={event=>setDraft({...draft,distributed_processes:Number(event.target.value)})} className="mt-1 w-full rounded border border-[#364357] bg-[#131822] px-2 py-1.5 text-xs text-white disabled:opacity-40"/></label></div>
            <p className="text-[10px] text-slate-500">공유와 MIG 배분은 연결 검사에서 기기 UUID와 용량이 확인돼야 합니다. 분산 학습은 분류·패치 분류·분할에서 지원합니다.</p>
            <button type="submit" disabled={isSaving} className="rounded bg-blue-700 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50">{isSaving ? '저장 중...' : '서버 설정 저장'}</button>
          </form>
        )}
        <ComputeJobsPanel onOpenReview={onClose}/>
      </section>
    </div>
  );
};
