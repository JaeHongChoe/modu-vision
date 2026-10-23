import React, { useEffect, useRef, useState } from 'react';
import { CopyPlus, Layers3, RefreshCw, Tags } from 'lucide-react';
import { api, type ProjectLabelSet } from '../../services/api';
import { useProjectStore } from '../../stores/useProjectStore';
import { ClassRenameDialog, DIALOG_TITLE as CLASS_RENAME_TITLE } from './ClassRenameDialog';

/** ``compact`` (focus editing): the label set in use, its switch and the class rename entry. ``collapsed`` (the simple
 * view): one line naming the label set in use that opens to the bar; the first labels never need another set. */
export const LabelSetBar: React.FC<{ compact?: boolean; collapsed?: boolean }> = ({ compact = false, collapsed = false }) => {
  const project = useProjectStore((state) => state.project);
  const busy = useProjectStore((state) => state.isProjectBusy);
  const projectError = useProjectStore((state) => state.projectError);
  const activate = useProjectStore((state) => state.activateLabelset);
  const createAndActivate = useProjectStore((state) => state.createAndActivateLabelset);
  const [sets, setSets] = useState<ProjectLabelSet[]>([]);
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const projectKey = project ? `${project.id}\0${project.project_dir}\0${project.active_labelset_id}` : null;
  const currentProjectKey = useRef(projectKey);
  currentProjectKey.current = projectKey;
  const requestSequence = useRef(0);

  const load = async () => {
    if (!project || projectKey !== currentProjectKey.current) return;
    const requestId = ++requestSequence.current;
    const isCurrent = () => requestId === requestSequence.current && projectKey === currentProjectKey.current;
    setLoading(true);
    setError(null);
    try {
      const response = await api.project.listLabelsets();
      if (isCurrent()) setSets(response.labelsets);
    } catch (caught) {
      if (isCurrent()) setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      if (isCurrent()) setLoading(false);
    }
  };

  useEffect(() => {
    setSets([]);
    setError(null);
    setLoading(false);
    void load();
    return () => { requestSequence.current += 1; };
  }, [projectKey]);

  const select = async (id: string) => {
    if (!project || id === project.active_labelset_id || busy) return;
    const ok = await activate(id);
    if (ok) await load();
  };

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim() || busy) return;
    const ok = await createAndActivate(name.trim());
    if (ok) {
      setName('');
      await load();
    }
  };

  if (!project) return null;
  const active = sets.find((item) => item.id === project.active_labelset_id);
  if (collapsed) return <details className="border-b border-[#34465B] bg-[#101D2C] px-4 py-1 text-slate-200">
    <summary className="cursor-pointer text-xs text-slate-300">라벨 세트: <span className="font-semibold text-slate-100">{active?.name || '기본 라벨'}</span> <span className="text-slate-500">({project.task === 'classification' ? '바꾸거나 새로 만들기' : '바꾸거나 새로 만들기 · 클래스 이름 변경'})</span></summary>
    <LabelSetBar compact={compact} />
  </details>;
  return <div className={`border-b border-[#34465B] bg-[#101D2C] px-4 ${compact ? 'py-1' : 'py-2.5'} text-slate-200`}>
    <div className="flex flex-wrap items-center gap-2.5">
      <span className="flex items-center gap-1.5 text-[10px] font-semibold tracking-[0.12em] text-cyan-300"><Layers3 className="h-3.5 w-3.5" /> 라벨 세트</span>
      <label className="sr-only" htmlFor="active-labelset">활성 레이블셋</label>
      <select id="active-labelset" value={project.active_labelset_id} disabled={busy || loading} onChange={(event) => void select(event.target.value)} className="min-w-[145px] max-w-[230px] rounded-md border border-[#41627A] bg-[#1A2E40] px-2.5 py-1.5 text-xs font-semibold text-white outline-none focus:border-cyan-400 disabled:opacity-50">
        {sets.length === 0 && <option value={project.active_labelset_id}>{active?.name || '레이블셋 불러오는 중'}</option>}
        {sets.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select>
      <button type="button" onClick={() => void load()} disabled={busy || loading} aria-label="레이블셋 목록 새로고침" className="rounded-md border border-[#3D5368] p-1.5 text-slate-300 hover:bg-[#273D52] disabled:opacity-50"><RefreshCw className="h-3.5 w-3.5" /></button>
      {!compact && <form onSubmit={create} className="ml-auto flex min-w-[260px] flex-1 items-center justify-end gap-2">
        <label className="sr-only" htmlFor="new-labelset-name">새 레이블셋 이름</label>
        <input id="new-labelset-name" value={name} maxLength={120} onChange={(event) => setName(event.target.value)} placeholder="새 검수안 이름" className="min-w-0 max-w-[210px] flex-1 rounded-md border border-[#3D5368] bg-[#0C1724] px-2.5 py-1.5 text-xs text-white outline-none placeholder:text-slate-500 focus:border-cyan-400" />
        <button type="submit" disabled={!name.trim() || busy} className="flex shrink-0 items-center gap-1.5 rounded-md border border-cyan-700/70 bg-cyan-950/40 px-2.5 py-1.5 text-xs font-medium text-cyan-200 hover:bg-cyan-900/40 disabled:opacity-40"><CopyPlus className="h-3.5 w-3.5" />현재 라벨 복제</button>
      </form>}
      {/* Rename or merge class names in this label set's edited labels (preview, backup, then apply); source files stay.
          Not offered for classification: its classes are folder names, which the rename never changes. */}
      {project.task !== 'classification' && <button type="button" onClick={() => setRenameOpen(true)} disabled={busy} title="활성 라벨 세트의 편집 라벨에서 클래스 이름을 바꾸거나 합칩니다. 원본 라벨 파일은 바꾸지 않습니다." className="flex shrink-0 items-center gap-1.5 rounded-md border border-[#3D5368] px-2.5 py-1.5 text-xs font-medium text-slate-200 hover:bg-[#273D52] disabled:opacity-40"><Tags className="h-3.5 w-3.5" />{CLASS_RENAME_TITLE}</button>}
    </div>
    {!compact && <p className="mt-1.5 text-[10px] leading-4 text-slate-500">활성 레이블셋의 편집 라벨만 학습·평가에 사용합니다. 전환 전 편집 내용은 저장합니다.</p>}
    {(error || projectError) && <p role="alert" className="mt-1.5 text-[11px] text-red-300">{error || projectError}</p>}
    {renameOpen && <ClassRenameDialog onClose={() => setRenameOpen(false)} />}
  </div>;
};
