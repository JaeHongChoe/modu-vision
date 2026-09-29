import React, { useEffect, useMemo, useState } from 'react';
import { CheckSquare2, Loader2, PauseCircle, Play, RefreshCw, Square } from 'lucide-react';
import { api, type LabelSuggestionBatch, type LabelSuggestionBatchEntry } from '../../services/api';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useProjectStore } from '../../stores/useProjectStore';
import type { ImageMeta } from '../../types';

function errorText(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (error && typeof error === 'object') {
    const detail = (error as Record<string, unknown>).detail;
    if (typeof detail === 'string') return detail;
  }
  return '일괄 제안을 처리하지 못했습니다.';
}

function statusText(status: LabelSuggestionBatch['status']): string {
  return ({ running: '생성 중', cancelling: '취소 중', cancelled: '취소됨', completed: '완료',
    failed: '중단됨', interrupted: '앱 재시작으로 중단됨' })[status];
}

function entryText(entry: LabelSuggestionBatchEntry): string {
  if (entry.status === 'generated') return `${entry.candidate_count}개 후보`;
  if (entry.status === 'zero_candidates') return '후보 0개';
  if (entry.status === 'failed') return '실패';
  return '대기';
}

interface Props {
  projectDir: string;
  modelId: string;
  threshold: number;
  disabled: boolean;
  onOpenEntry: (entry: LabelSuggestionBatchEntry) => Promise<void>;
  onRunningChange: (running: boolean) => void;
}

export const BulkLabelAssist: React.FC<Props> = ({ projectDir, modelId, threshold, disabled, onOpenEntry, onRunningChange }) => {
  const folderPath = useDatasetStore((state) => state.folderPath);
  const task = useProjectStore((state) => state.task);
  const [mode, setMode] = useState<'unlabeled' | 'selected'>('unlabeled');
  const [gallery, setGallery] = useState<ImageMeta[]>([]);
  const [galleryBusy, setGalleryBusy] = useState(false);
  const [search, setSearch] = useState('');
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set());
  const [batches, setBatches] = useState<LabelSuggestionBatch[]>([]);
  const [selectedBatchId, setSelectedBatchId] = useState('');
  const [busy, setBusy] = useState<'start' | 'cancel' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selectedBatch = batches.find((batch) => batch.id === selectedBatchId);
  const activeBatch = batches.find((batch) => batch.status === 'running' || batch.status === 'cancelling');
  const running = Boolean(activeBatch);
  const filteredGallery = useMemo(() => gallery.filter((item) =>
    item.file_name.toLowerCase().includes(search.toLowerCase())), [gallery, search]);

  useEffect(() => {
    onRunningChange(running);
    return () => onRunningChange(false);
  }, [running, onRunningChange]);

  useEffect(() => {
    let cancelled = false;
    setGallery([]);
    setSelectedPaths(new Set());
    setBatches([]);
    setSelectedBatchId('');
    setError(null);
    void api.labelSuggestions.listBatches()
      .then((result) => {
        if (cancelled || useProjectStore.getState().projectDir !== projectDir) return;
        setBatches(result.batches);
        setSelectedBatchId(result.batches[0]?.id || '');
      })
      .catch((cause) => { if (!cancelled) setError(errorText(cause)); });
    return () => { cancelled = true; };
  }, [projectDir]);

  useEffect(() => {
    if (!activeBatch?.id) return;
    const activeBatchId = activeBatch.id;
    let cancelled = false;
    const poll = async () => {
      try {
        const next = await api.labelSuggestions.getBatch(activeBatchId);
        if (cancelled || useProjectStore.getState().projectDir !== projectDir) return;
        setBatches((previous) => previous.map((item) => item.id === next.id ? next : item));
      } catch (cause) {
        if (!cancelled) setError(errorText(cause));
      }
    };
    const timer = window.setInterval(() => void poll(), 750);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [projectDir, activeBatch?.id]);

  const loadGallery = async () => {
    if (!folderPath || !projectDir) return;
    setGalleryBusy(true);
    setError(null);
    try {
      const images: ImageMeta[] = [];
      let total = Infinity;
      while (images.length < total && images.length < 5000) {
        const page = await api.dataset.getImages({ folder_path: folderPath, task, limit: 500, offset: images.length });
        if (useProjectStore.getState().projectDir !== projectDir) return;
        images.push(...page.items);
        total = page.total;
        if (!page.items.length) break;
      }
      setGallery(images);
      if (images.length < total) setError('최대 5,000개 이미지만 목록에 표시됩니다. 범위를 나눠 선택하세요.');
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      if (useProjectStore.getState().projectDir === projectDir) setGalleryBusy(false);
    }
  };

  const start = async () => {
    if (!modelId || disabled || busy || !projectDir || (mode === 'selected' && !selectedPaths.size)) return;
    setBusy('start');
    setError(null);
    try {
      const batch = await api.labelSuggestions.startBatch({
        job_id: modelId, threshold,
        ...(mode === 'selected' ? { image_paths: [...selectedPaths] } : {}),
      });
      if (useProjectStore.getState().projectDir !== projectDir) return;
      setBatches((previous) => [batch, ...previous]);
      setSelectedBatchId(batch.id);
    } catch (cause) {
      if (useProjectStore.getState().projectDir === projectDir) setError(errorText(cause));
    } finally {
      if (useProjectStore.getState().projectDir === projectDir) setBusy(null);
    }
  };

  const cancel = async () => {
    if (!activeBatch || busy) return;
    setBusy('cancel');
    setError(null);
    try {
      const next = await api.labelSuggestions.cancelBatch(activeBatch.id);
      if (useProjectStore.getState().projectDir === projectDir) {
        setBatches((previous) => previous.map((item) => item.id === next.id ? next : item));
      }
    } catch (cause) {
      if (useProjectStore.getState().projectDir === projectDir) setError(errorText(cause));
    } finally {
      if (useProjectStore.getState().projectDir === projectDir) setBusy(null);
    }
  };

  return (
    <section className="space-y-3 rounded-lg border border-indigo-700/60 bg-[#101722] p-3" aria-label="여러 이미지 자동 라벨 후보">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h4 className="font-semibold text-indigo-100">여러 이미지 후보 생성</h4>
          <p className="text-[11px] text-slate-400">순서대로 예측하고 이미지별 검토 대기열에 저장합니다.</p>
        </div>
        <span className="rounded border border-indigo-700 bg-indigo-950/50 px-2 py-1 text-[10px] text-indigo-200">자동 채택 없음</span>
      </div>
      <div className="grid grid-cols-2 gap-2 text-[11px]">
        <button type="button" onClick={() => setMode('unlabeled')} className={`rounded border px-2 py-2 text-left ${mode === 'unlabeled' ? 'border-indigo-400 bg-indigo-900/30 text-white' : 'border-slate-700 text-slate-400'}`}>
          미라벨 전체 <span className="block text-[10px] opacity-70">평면 LabelMe 데이터의 미라벨 이미지</span>
        </button>
        <button type="button" onClick={() => { setMode('selected'); if (!gallery.length) void loadGallery(); }} className={`rounded border px-2 py-2 text-left ${mode === 'selected' ? 'border-indigo-400 bg-indigo-900/30 text-white' : 'border-slate-700 text-slate-400'}`}>
          직접 선택 <span className="block text-[10px] opacity-70">COCO·YOLO·폴더형 데이터도 선택 가능</span>
        </button>
      </div>
      {mode === 'selected' && <div className="space-y-2 rounded border border-slate-700 p-2">
        <div className="flex gap-1">
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="이미지 파일명 검색" aria-label="일괄 이미지 검색" className="min-w-0 grow rounded border border-slate-600 bg-[#1D2938] px-2 py-1 text-[11px] text-white" />
          <button type="button" onClick={() => void loadGallery()} disabled={galleryBusy} aria-label="이미지 목록 새로고침" className="rounded border border-slate-600 px-2 text-slate-300 disabled:opacity-40"><RefreshCw className={`h-3.5 w-3.5 ${galleryBusy ? 'animate-spin' : ''}`} /></button>
        </div>
        <div className="flex items-center justify-between text-[10px] text-slate-400">
          <span>{selectedPaths.size}개 선택 · {gallery.length}개 목록</span>
          <div className="flex gap-2">
            <button type="button" onClick={() => setSelectedPaths((previous) => new Set([...previous, ...filteredGallery.map((item) => item.file_path)]))} className="text-cyan-300 hover:text-white">보이는 항목 선택</button>
            <button type="button" onClick={() => setSelectedPaths(new Set())} className="hover:text-white">선택 해제</button>
          </div>
        </div>
        <div className="max-h-36 space-y-0.5 overflow-y-auto" aria-label="일괄 생성 이미지 선택">
          {galleryBusy && <p className="p-2 text-slate-400">이미지 목록을 불러오는 중...</p>}
          {!galleryBusy && !filteredGallery.length && <p className="p-2 text-slate-500">표시할 이미지가 없습니다.</p>}
          {filteredGallery.map((item) => <label key={item.file_path} className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-slate-300 hover:bg-slate-800">
            {selectedPaths.has(item.file_path) ? <CheckSquare2 className="h-3.5 w-3.5 text-cyan-400" /> : <Square className="h-3.5 w-3.5 text-slate-500" />}
            <input type="checkbox" checked={selectedPaths.has(item.file_path)} onChange={(event) => setSelectedPaths((previous) => {
              const next = new Set(previous);
              if (event.target.checked) next.add(item.file_path); else next.delete(item.file_path);
              return next;
            })} className="sr-only" />
            <span className="truncate">{item.file_name}</span>
          </label>)}
        </div>
      </div>}
      <div className="flex gap-2">
        <button type="button" onClick={() => void start()} disabled={disabled || !modelId || !!busy || !!running || (mode === 'selected' && !selectedPaths.size)} className="flex grow items-center justify-center gap-1.5 rounded bg-indigo-600 px-3 py-2 font-semibold text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40">
          {busy === 'start' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />} 후보 생성 시작
        </button>
        {running && <button type="button" onClick={() => void cancel()} disabled={!!busy || activeBatch?.status === 'cancelling'} className="flex items-center gap-1 rounded border border-amber-600 px-3 py-2 font-semibold text-amber-200 hover:bg-amber-950/40 disabled:opacity-40"><PauseCircle className="h-3.5 w-3.5" /> 취소</button>}
      </div>
      {error && <p role="alert" className="rounded border border-red-800 bg-red-950/30 p-2 text-[11px] text-red-200">{error}</p>}
      {batches.length > 0 && <div className="space-y-2 border-t border-slate-700 pt-3">
        <div className="flex items-center justify-between">
          <h5 className="font-semibold text-slate-200">검토 대기열</h5>
          <select value={selectedBatchId} onChange={(event) => setSelectedBatchId(event.target.value)} aria-label="일괄 제안 기록" className="max-w-[230px] rounded border border-slate-600 bg-[#1D2938] px-2 py-1 text-[10px] text-slate-200">
            {batches.map((item) => <option key={item.id} value={item.id}>{new Date(item.created_at).toLocaleString('ko-KR')} · {statusText(item.status)}</option>)}
          </select>
        </div>
        {selectedBatch && <>
          <div className="space-y-1 rounded border border-slate-700 bg-[#0B0E14] p-2 text-[11px]">
            <div className="flex justify-between"><span>{statusText(selectedBatch.status)}</span><span className="font-mono text-cyan-300">{selectedBatch.processed} / {selectedBatch.total}</span></div>
            <div className="h-1.5 overflow-hidden rounded bg-slate-700"><div className="h-full bg-cyan-500 transition-all" style={{ width: `${selectedBatch.total ? 100 * selectedBatch.processed / selectedBatch.total : 0}%` }} /></div>
            <p className="text-slate-400">후보 있는 이미지 {selectedBatch.generated} · 후보 0개 {selectedBatch.zero_candidates} · 실패 {selectedBatch.failed}</p>
            {selectedBatch.error && <p className="text-red-300">{selectedBatch.error}</p>}
          </div>
          <div className="max-h-40 space-y-1 overflow-y-auto">
            {selectedBatch.entries.map((entry, index) => <div key={`${entry.image_path}-${index}`} className="flex items-center gap-2 rounded border border-slate-700 bg-[#17202D] px-2 py-1.5 text-[11px]">
              <span className="min-w-0 grow truncate text-slate-200" title={entry.image_path}>{entry.image_path.split(/[\\/]/).pop()}</span>
              <span className={entry.status === 'failed' ? 'text-red-300' : entry.status === 'generated' ? 'text-cyan-300' : 'text-slate-400'}>{entryText(entry)}</span>
              {entry.proposal_id && <button type="button" onClick={() => void onOpenEntry(entry)} className="rounded border border-cyan-700 px-2 py-1 font-semibold text-cyan-200 hover:bg-cyan-950">검토</button>}
              {entry.error && <span title={entry.error} className="max-w-20 truncate text-red-300">{entry.error}</span>}
            </div>)}
          </div>
        </>}
      </div>}
      <p className="text-[10px] leading-relaxed text-slate-500">생성된 후보는 프로젝트에 저장됩니다. 채택할 이미지를 열어 각 후보를 확인하세요. 작업을 취소하면 완료된 후보는 유지됩니다. 생성 중 작업 유형이나 데이터 출처를 변경하면 배치가 중단됩니다.</p>
    </section>
  );
};

export default BulkLabelAssist;
