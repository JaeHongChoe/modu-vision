import React, { useEffect, useState } from 'react';
import { CopyPlus, Layers3, RefreshCw } from 'lucide-react';
import { api, type ProjectLabelSet } from '../../services/api';
import { useProjectStore } from '../../stores/useProjectStore';

export const LabelSetBar: React.FC = () => {
  const project = useProjectStore((state) => state.project);
  const busy = useProjectStore((state) => state.isProjectBusy);
  const projectError = useProjectStore((state) => state.projectError);
  const activate = useProjectStore((state) => state.activateLabelset);
  const createAndActivate = useProjectStore((state) => state.createAndActivateLabelset);
  const [sets, setSets] = useState<ProjectLabelSet[]>([]);
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    if (!project) return;
    setLoading(true);
    setError(null);
    try {
      const response = await api.project.listLabelsets();
      setSets(response.labelsets);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, [project?.project_dir, project?.active_labelset_id]);

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
  return <div className="border-b border-[#34465B] bg-[#101D2C] px-4 py-2.5 text-slate-200">
    <div className="flex flex-wrap items-center gap-2.5">
      <span className="flex items-center gap-1.5 text-[10px] font-semibold tracking-[0.12em] text-cyan-300"><Layers3 className="h-3.5 w-3.5" /> LABEL SET</span>
      <label className="sr-only" htmlFor="active-labelset">활성 레이블셋</label>
      <select id="active-labelset" value={project.active_labelset_id} disabled={busy || loading} onChange={(event) => void select(event.target.value)} className="min-w-[145px] max-w-[230px] rounded-md border border-[#41627A] bg-[#1A2E40] px-2.5 py-1.5 text-xs font-semibold text-white outline-none focus:border-cyan-400 disabled:opacity-50">
        {sets.length === 0 && <option value={project.active_labelset_id}>{active?.name || '레이블셋 불러오는 중'}</option>}
        {sets.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select>
      <button type="button" onClick={() => void load()} disabled={busy || loading} aria-label="레이블셋 목록 새로고침" className="rounded-md border border-[#3D5368] p-1.5 text-slate-300 hover:bg-[#273D52] disabled:opacity-50"><RefreshCw className="h-3.5 w-3.5" /></button>
      <form onSubmit={create} className="ml-auto flex min-w-[260px] flex-1 items-center justify-end gap-2">
        <label className="sr-only" htmlFor="new-labelset-name">새 레이블셋 이름</label>
        <input id="new-labelset-name" value={name} maxLength={120} onChange={(event) => setName(event.target.value)} placeholder="새 검수안 이름" className="min-w-0 max-w-[210px] flex-1 rounded-md border border-[#3D5368] bg-[#0C1724] px-2.5 py-1.5 text-xs text-white outline-none placeholder:text-slate-500 focus:border-cyan-400" />
        <button type="submit" disabled={!name.trim() || busy} className="flex shrink-0 items-center gap-1.5 rounded-md border border-cyan-700/70 bg-cyan-950/40 px-2.5 py-1.5 text-xs font-medium text-cyan-200 hover:bg-cyan-900/40 disabled:opacity-40"><CopyPlus className="h-3.5 w-3.5" />현재 라벨 복제</button>
      </form>
    </div>
    <p className="mt-1.5 text-[10px] leading-4 text-slate-500">활성 레이블셋의 편집 라벨만 학습·평가에 사용합니다. 전환 전 편집 내용은 저장합니다.</p>
    {(error || projectError) && <p role="alert" className="mt-1.5 text-[11px] text-red-300">{error || projectError}</p>}
  </div>;
};
