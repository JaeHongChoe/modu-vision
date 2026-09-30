import React, { useEffect, useState } from 'react';
import { ArchiveRestore, CheckCircle2, Clock3, Database, FileCheck2, FolderArchive, RefreshCw, ShieldCheck, X } from 'lucide-react';
import { api, type DatasetVersionSummary, type DatasetVersionVerification } from '../../services/api';

interface Props {
  datasetPath: string;
  onClose: () => void;
  onRestored: () => Promise<void>;
}

function sizeText(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function statusText(status: string): string {
  if (status === 'verified') return '원본 검증 완료';
  if (status === 'changed') return '원본/백업 변경됨';
  if (status === 'corrupt') return '버전 기록 손상';
  return '검증 필요';
}

export const DatasetVersionPanel: React.FC<Props> = ({ datasetPath, onClose, onRestored }) => {
  const [versions, setVersions] = useState<DatasetVersionSummary[]>([]);
  const [checks, setChecks] = useState<Partial<Record<string, DatasetVersionVerification>>>({});
  const [name, setName] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState<string | null>('list');
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [confirmRestoreId, setConfirmRestoreId] = useState<string | null>(null);

  const load = async () => {
    setBusy('list');
    setError(null);
    try {
      const response = await api.datasetVersions.list();
      setVersions(response.versions);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(null);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onClose();
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [busy, onClose]);

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim() || busy) return;
    setBusy('create');
    setError(null);
    setMessage(null);
    try {
      const created = await api.datasetVersions.create({ name: name.trim(), note: note.trim(), dataset_path: datasetPath });
      setVersions((current) => [created, ...current]);
      setName('');
      setNote('');
      setMessage(`버전 “${created.name}”을 저장했습니다. 이미지 ${created.image_count}개의 내용 해시와 원본 경로를 기록했습니다.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(null);
    }
  };

  const verify = async (id: string) => {
    if (busy) return;
    setBusy(`verify:${id}`);
    setError(null);
    setMessage(null);
    try {
      const result = await api.datasetVersions.verify(id);
      setChecks((current) => ({ ...current, [id]: result }));
      setMessage(result.status === 'verified'
        ? `원본/보관 파일 ${result.checked_file_count}개를 확인했습니다. 현재 편집된 파일 ${result.editable_changed_files.length}개는 복원 시 교체됩니다.`
        : `원본 또는 보관 파일 ${result.changed_files.length}개가 버전과 다릅니다. 복원을 막았습니다.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(null);
    }
  };

  const restore = async (id: string) => {
    if (busy) return;
    if (confirmRestoreId !== id) {
      setConfirmRestoreId(id);
      setError(null);
      return;
    }
    setBusy(`restore:${id}`);
    setError(null);
    setMessage(null);
    try {
      const result = await api.datasetVersions.restore(id);
      setConfirmRestoreId(null);
      let refreshWarning = '';
      try {
        await onRestored();
      } catch (caught) {
        refreshWarning = ` 화면 데이터 새로고침은 실패했습니다: ${caught instanceof Error ? caught.message : String(caught)}`;
      }
      try {
        const updated = await api.datasetVersions.list();
        setVersions(updated.versions);
        setChecks({});
      } catch (caught) {
        refreshWarning += ` 버전 목록 새로고침은 실패했습니다: ${caught instanceof Error ? caught.message : String(caught)}`;
      }
      setMessage(`라벨/분할을 복원했습니다. 변경 전 상태는 자동 백업 ${result.backup_version_id}로 보관했습니다.${refreshWarning}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-[#040912]/80 p-5" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !busy) onClose();
    }}>
      <div role="dialog" aria-modal="true" aria-label="데이터와 라벨 버전" className="flex max-h-full w-full max-w-[960px] flex-col overflow-hidden rounded-xl border border-[#33465C] bg-[#111B29] shadow-[0_30px_90px_rgba(0,0,0,0.55)]">
        <div className="flex items-start justify-between border-b border-[#33465C] bg-[#17263A] px-7 py-6">
          <div>
            <div className="mb-2 flex items-center gap-2 text-[10px] font-bold tracking-[0.18em] text-sky-300"><Database className="h-4 w-4" /> DATA & LABEL VERSIONS</div>
            <h2 className="text-xl font-semibold text-white">데이터와 라벨 버전</h2>
            <p className="mt-1.5 max-w-[680px] text-xs leading-5 text-slate-400">이미지는 복사하지 않고 원본 경로와 SHA-256을 기록합니다. 라벨과 분할 정보는 프로젝트에 보관합니다.</p>
          </div>
          <button type="button" onClick={onClose} disabled={Boolean(busy)} aria-label="버전 관리 닫기" className="rounded-md border border-[#43576F] p-1.5 text-slate-300 hover:bg-[#26394D] disabled:opacity-40"><X className="h-4 w-4" /></button>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-[280px_minmax(0,1fr)]">
          <aside className="overflow-y-auto border-r border-[#33465C] bg-[#111A27] p-5">
            <div className="mb-4 rounded-lg border border-[#344963] bg-[#18283B] p-3">
              <div className="mb-1 text-[10px] font-bold uppercase tracking-widest text-sky-300">SOURCE DATASET</div>
              <div className="break-all font-mono text-[10px] leading-5 text-slate-300">{datasetPath}</div>
            </div>
            <form onSubmit={create} className="space-y-3">
              <div><h3 className="text-sm font-semibold text-white">새 버전 저장</h3><p className="mt-1 text-xs leading-5 text-slate-500">라벨 수정이나 분할 변경 전에 현재 상태를 남겨두세요.</p></div>
              <label className="block text-xs font-medium text-slate-300">버전 이름<input value={name} onChange={(event) => setName(event.target.value)} maxLength={120} placeholder="예: 1차 검수 완료" className="mt-1.5 w-full rounded-md border border-[#3E526A] bg-[#0C1521] px-3 py-2.5 text-sm text-white outline-none placeholder:text-slate-400 focus:border-sky-500" /></label>
              <label className="block text-xs font-medium text-slate-300">메모<textarea value={note} onChange={(event) => setNote(event.target.value)} maxLength={2000} rows={3} placeholder="변경 내용이나 검수 기준" className="mt-1.5 w-full resize-none rounded-md border border-[#3E526A] bg-[#0C1521] px-3 py-2 text-xs leading-5 text-white outline-none placeholder:text-slate-400 focus:border-sky-500" /></label>
              <button type="submit" disabled={!name.trim() || Boolean(busy)} className="flex w-full items-center justify-center gap-2 rounded-md bg-sky-600 px-3 py-2.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-40"><FolderArchive className="h-4 w-4" />{busy === 'create' ? '해시 확인 및 저장 중...' : '현재 상태를 버전으로 저장'}</button>
            </form>
            <div className="mt-5 flex items-start gap-2 rounded-md border border-sky-800/50 bg-sky-950/20 p-3 text-[11px] leading-5 text-slate-400"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-sky-400" /><span>복원 전 현재 편집 상태를 자동 백업합니다. 원본 LabelMe 파일과 원본 이미지는 수정하지 않습니다.</span></div>
          </aside>

          <div className="min-h-0 overflow-y-auto p-6">
            <div className="mb-4 flex items-center justify-between"><div><h3 className="text-sm font-semibold text-slate-100">저장된 버전</h3><p className="mt-1 text-xs text-slate-500">검증하면 원본 변경과 현재 편집 상태의 차이를 확인합니다.</p></div><button type="button" onClick={() => void load()} disabled={Boolean(busy)} className="flex items-center gap-1.5 rounded-md border border-[#3C5069] px-2.5 py-1.5 text-[11px] text-slate-300 hover:bg-[#223247] disabled:opacity-40"><RefreshCw className="h-3.5 w-3.5" />목록 새로고침</button></div>
            {busy === 'list' && <div className="py-8 text-center text-xs text-slate-400">버전 목록을 불러오는 중입니다...</div>}
            {!busy && versions.length === 0 && <div className="rounded-lg border border-dashed border-[#3D5066] px-4 py-10 text-center text-xs text-slate-400">저장된 버전이 없습니다. 왼쪽에서 첫 버전을 만드세요.</div>}
            <div className="space-y-3">
              {versions.map((item) => {
                const check = checks[item.id];
                const status = check?.status || item.status;
                const confirm = confirmRestoreId === item.id;
                return <div key={item.id} className={`rounded-lg border p-4 ${confirm ? 'border-amber-500/70 bg-[#30291E]' : 'border-[#354960] bg-[#172538]'}`}>
                  <div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className="text-sm font-semibold text-white">{item.name}</span>{item.kind === 'auto_backup' && <span className="rounded border border-amber-700/60 px-1.5 py-0.5 text-[9px] font-bold text-amber-300">자동 백업</span>}</div><div className="mt-1.5 flex items-center gap-1.5 text-[10px] text-slate-500"><Clock3 className="h-3 w-3" />{item.created_at.replace('T', ' ').replace('Z', ' UTC')} · {item.id}</div></div><span className={`shrink-0 rounded border px-2 py-1 text-[10px] font-medium ${status === 'verified' ? 'border-emerald-700/50 bg-emerald-950/30 text-emerald-300' : status === 'changed' || status === 'corrupt' ? 'border-red-700/50 bg-red-950/30 text-red-300' : 'border-[#52647B] text-slate-400'}`}>{statusText(status)}</span></div>
                  {item.note && <p className="mt-2 text-xs leading-5 text-slate-400">{item.note}</p>}
                  <div className="mt-3 grid grid-cols-3 gap-2 text-[11px]"><div className="rounded border border-[#33475E] bg-[#101B2A] px-2.5 py-2"><span className="block text-[9px] text-slate-500">원본 이미지</span><strong className="mt-0.5 block font-mono text-slate-100">{item.image_count ?? '—'}장</strong></div><div className="rounded border border-[#33475E] bg-[#101B2A] px-2.5 py-2"><span className="block text-[9px] text-slate-500">보관된 라벨 파일</span><strong className="mt-0.5 block font-mono text-slate-100">{item.label_file_count ?? '—'}개</strong></div><div className="rounded border border-[#33475E] bg-[#101B2A] px-2.5 py-2"><span className="block text-[9px] text-slate-500">이미지 원본 크기</span><strong className="mt-0.5 block font-mono text-slate-100">{typeof item.total_image_bytes === 'number' ? sizeText(item.total_image_bytes) : '—'}</strong></div></div>
                  {check && <div className="mt-3 rounded border border-[#34495E] bg-[#112031] px-3 py-2 text-[11px] leading-5 text-slate-300"><div className="flex items-center gap-1.5"><FileCheck2 className="h-3.5 w-3.5 text-sky-400" />검증 파일 {check.checked_file_count}개 · 현재 편집과 다른 파일 {check.editable_changed_files.length}개</div>{check.changed_files.length > 0 && <div className="mt-1 break-all text-red-300">변경/손상: {check.changed_files.slice(0, 3).join(', ')}{check.changed_files.length > 3 ? ` 외 ${check.changed_files.length - 3}개` : ''}</div>}</div>}
                  {confirm && <div className="mt-3 rounded border border-amber-600/60 bg-amber-950/30 p-3 text-[11px] leading-5 text-amber-200">이 버전의 Studio 라벨과 분할로 되돌립니다. 현재 상태는 자동 백업하며 원본 LabelMe 파일은 그대로 둡니다.</div>}
                  <div className="mt-3 flex justify-end gap-2"><button type="button" disabled={Boolean(busy) || status === 'corrupt'} onClick={() => void verify(item.id)} className="flex items-center gap-1 rounded border border-[#496079] px-2.5 py-1.5 text-[11px] text-slate-200 hover:bg-[#29405A] disabled:opacity-40"><CheckCircle2 className="h-3.5 w-3.5" />{busy === `verify:${item.id}` ? '검증 중...' : '원본 검증'}</button>{confirm && <button type="button" onClick={() => setConfirmRestoreId(null)} className="rounded border border-[#6D6559] px-2.5 py-1.5 text-[11px] text-slate-200 hover:bg-[#4C4030]">취소</button>}<button type="button" disabled={Boolean(busy) || status === 'changed' || status === 'corrupt'} onClick={() => void restore(item.id)} className="flex items-center gap-1 rounded border border-amber-600/70 bg-amber-900/20 px-2.5 py-1.5 text-[11px] font-semibold text-amber-200 hover:bg-amber-800/30 disabled:opacity-40"><ArchiveRestore className="h-3.5 w-3.5" />{busy === `restore:${item.id}` ? '복원 중...' : confirm ? '복원 실행' : '복원'}</button></div>
                </div>;
              })}
            </div>
            {error && <div role="alert" className="mt-4 rounded-md border border-red-700/60 bg-red-950/30 px-3 py-2 text-xs leading-5 text-red-200">{error}</div>}
            {message && <div role="status" className="mt-4 rounded-md border border-emerald-700/50 bg-emerald-950/20 px-3 py-2 text-xs leading-5 text-emerald-200">{message}</div>}
          </div>
        </div>
      </div>
    </div>
  );
};
