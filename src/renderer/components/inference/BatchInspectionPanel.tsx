import React, { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Image as ImageIcon, Play, Square, Layers } from 'lucide-react';
import { api, resolveApiUrl } from '../../services/api';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useFlowchartStore } from '../../stores/useFlowchartStore';
import { useProjectStore } from '../../stores/useProjectStore';
import {
  batchSourceResetKey, filterBatchRows, isBatchSourceCurrent, isBatchSourceReady,
  runBatchInspection, summarizeBatch,
  type BatchSourceState,
  type BatchFilter, type BatchInspectionReport, type BatchScope, type BatchStopReason,
} from './batchInspection';

const scopeNames: Record<BatchScope, string> = {
  test: '테스트 분할', val: '검증 분할', train: '학습 분할', all: '전체 이미지',
};
const stateNames: Record<string, string> = {
  pending: '대기', running: '검사 중', skipped: '미실행',
  OK: 'OK', NG: 'NG', REVIEW: '검토', error: '실패',
};
const stateColors: Record<string, string> = {
  pending: 'text-slate-400 border-slate-600',
  running: 'text-cyan-300 border-cyan-600',
  skipped: 'text-slate-400 border-slate-600',
  OK: 'text-emerald-300 border-emerald-600',
  NG: 'text-rose-300 border-rose-600',
  REVIEW: 'text-amber-300 border-amber-600',
  error: 'text-red-300 border-red-600',
};

export const BatchInspectionPanel: React.FC = () => {
  const task = useProjectStore((state) => state.task);
  const { folderPath, datasetKey, hasSelectedFolder, importError, isLoading: datasetIsLoading,
    isSplitting, split, sourceImages } = useDatasetStore();
  const contextRevision = useFlowchartStore((state) => state.contextRevision);
  const [scope, setScope] = useState<BatchScope>('test');
  const [report, setReport] = useState<BatchInspectionReport | null>(null);
  const [filter, setFilter] = useState<BatchFilter>('all');
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [stopRequested, setStopRequested] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stopReasonRef = useRef<BatchStopReason>(null);
  const currentBatchSource = (): BatchSourceState => {
    const dataset = useDatasetStore.getState();
    return {
      folderPath: dataset.folderPath,
      task: useProjectStore.getState().task,
      datasetKey: dataset.datasetKey,
      contextRevision: useFlowchartStore.getState().contextRevision,
      hasSelectedFolder: dataset.hasSelectedFolder,
      importError: dataset.importError,
      isLoading: dataset.isLoading,
      isSplitting: dataset.isSplitting,
    };
  };
  const renderedSource: BatchSourceState = {
    folderPath, task, datasetKey, contextRevision, hasSelectedFolder,
    importError, isLoading: datasetIsLoading, isSplitting,
  };
  const sourceReady = isBatchSourceReady(renderedSource);
  const resetKey = batchSourceResetKey(renderedSource);

  useEffect(() => {
    stopReasonRef.current = 'source_changed';
    setStopRequested(false);
    setReport(null);
    setSelectedPath(null);
    setFilter('all');
    setScope('test');
    setError(null);
  }, [resetKey]);

  const handleStart = async () => {
    const startedSource = currentBatchSource();
    if (!isBatchSourceReady(startedSource) || isRunning) return;
    const sourceFolder = startedSource.folderPath;
    const sourceTask = startedSource.task;
    const selectedScope = scope;
    const currentSource = () => isBatchSourceCurrent(currentBatchSource(), startedSource);
    stopReasonRef.current = null;
    setStopRequested(false);
    setIsRunning(true);
    setReport(null);
    setSelectedPath(null);
    setFilter('all');
    setError(null);
    try {
      const finished = await runBatchInspection({
        sourceFolder, task: sourceTask, scope: selectedScope,
        stopReason: () => currentSource() ? stopReasonRef.current : 'source_changed',
        onUpdate: (next) => {
          if (!currentSource()) return;
          setReport(next);
          setSelectedPath((current) => current || next.rows.find((row) =>
            row.state === 'OK' || row.state === 'NG' || row.state === 'REVIEW' || row.state === 'error'
          )?.image.file_path || null);
        },
      }, {
        getPipeline: api.flowchart.getPipeline,
        verifyModels: api.flowchart.verifyModels,
        getImages: api.dataset.getImages,
        run: api.flowchart.run,
      });
      if (currentSource()) setReport(finished);
    } catch (cause) {
      if (currentSource() && stopReasonRef.current !== 'user_stop') {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      setIsRunning(false);
    }
  };

  const rows = report?.rows ?? [];
  const summary = summarizeBatch(rows);
  const visibleRows = filterBatchRows(rows, filter);
  const selected = visibleRows.find((row) => row.image.file_path === selectedPath) ?? visibleRows[0] ?? null;
  const selectFilter = (next: BatchFilter) => {
    setFilter(next);
    setSelectedPath(filterBatchRows(rows, next)[0]?.image.file_path ?? null);
  };
  const progress = summary.total ? summary.processed + summary.errors : 0;

  return (
    <section className="border border-[#2B3547] bg-[#131822] rounded p-4 space-y-4" aria-label="실제 이미지 일괄 검사">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[#2B3547] pb-3">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-bold text-slate-100">
            <Layers className="w-4 h-4 text-cyan-400" /> 실제 이미지 일괄 검사
          </h3>
          <p className="mt-1 text-xs text-slate-400">
            5단계에서 저장한 플로우를 선택한 데이터 분할에 순서대로 실행합니다. 원격 모델은 학습한 서버에서 검사하며, 상단 Compute 선택은 새 학습 대상입니다. 이미지별 판정과 중간 노드 기록을 확인하세요.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="batch-inspection-scope" className="text-xs text-slate-300">검사 범위</label>
          <select
            id="batch-inspection-scope"
            value={scope}
            onChange={(event) => setScope(event.target.value as BatchScope)}
            disabled={isRunning}
            className="rounded border border-[#364357] bg-[#0B0E14] px-2 py-1.5 text-xs text-white disabled:opacity-50"
          >
            <option value="test">테스트 ({split.test})</option>
            <option value="val">검증 ({split.val})</option>
            <option value="train">학습 ({split.train})</option>
            <option value="all">전체 ({sourceImages}) · 직접 선택</option>
          </select>
          <button
            type="button"
            onClick={handleStart}
            disabled={!sourceReady || isRunning}
            className="flex items-center gap-1.5 rounded border border-cyan-600 bg-cyan-900/50 px-3 py-1.5 text-xs font-semibold text-cyan-100 hover:bg-cyan-800/60 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Play className="w-3.5 h-3.5" /> 검사 시작
          </button>
          {isRunning && (
            <button
              type="button"
              onClick={() => { stopReasonRef.current = 'user_stop'; setStopRequested(true); }}
              disabled={stopRequested}
              className="flex items-center gap-1.5 rounded border border-amber-600 bg-amber-950/50 px-3 py-1.5 text-xs font-semibold text-amber-200 disabled:opacity-50"
            >
              <Square className="w-3.5 h-3.5" /> 중단
            </button>
          )}
        </div>
      </div>

      {!sourceReady && (
        <div role="status" className="text-xs text-amber-300">1단계에서 현재 작업 유형의 데이터 폴더를 불러오면 실제 이미지 검사를 시작할 수 있습니다.</div>
      )}
      {scope === 'all' && !isRunning && (
        <div className="rounded border border-amber-700/50 bg-amber-950/20 px-3 py-2 text-xs text-amber-200">
          전체 이미지는 선택한 폴더의 모든 이미지를 검사하며, 원격 서버에서는 이미지마다 왕복 시간이 듭니다.
        </div>
      )}
      {isRunning && (
        <div role="status" className="text-xs text-cyan-300">
          {stopRequested
            ? '현재 이미지 검사 후 중단합니다. 남은 이미지는 미실행으로 표시됩니다.'
            : `검사 진행 중 ${progress} / ${summary.total || '목록 준비 중'}`}
        </div>
      )}
      {error && <div role="alert" className="flex items-start gap-2 rounded border border-red-700 bg-red-950/30 p-2 text-xs text-red-200"><AlertTriangle className="h-4 w-4 shrink-0" />{error}</div>}

      {report && (
        <>
          <div className="space-y-2">
            <div className="flex flex-wrap justify-between gap-2 text-xs text-slate-300">
              <span>플로우: <strong className="text-slate-100">{report.pipeline_name}</strong> · 범위: {scopeNames[report.scope]}</span>
              <span>{report.status === 'completed' ? '완료' : report.status === 'stopped' ? '중단' : '실행 중'} · {progress}/{summary.total} 확인</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded bg-[#0B0E14]" role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={summary.total}>
              <div className="h-full bg-cyan-500 transition-all" style={{ width: `${summary.total ? progress / summary.total * 100 : 0}%` }} />
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5" aria-label="검사 결과 필터">
            {([
              ['all', `전체 ${summary.total}`], ['OK', `OK ${summary.ok}`], ['NG', `NG ${summary.ng}`],
              ['REVIEW', `검토 ${summary.review}`], ['error', `실패 ${summary.errors}`], ['unrun', `미실행 ${summary.unrun}`],
            ] as Array<[BatchFilter, string]>).map(([key, label]) => (
              <button
                key={key} type="button" onClick={() => selectFilter(key)}
                aria-pressed={filter === key}
                className={`rounded border px-2 py-1 text-[11px] ${filter === key ? 'border-cyan-500 bg-cyan-900/40 text-cyan-100' : 'border-[#364357] text-slate-300 hover:bg-[#222B3D]'}`}
              >{label}</button>
            ))}
          </div>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(280px,0.85fr)_minmax(0,1.15fr)]">
            <div className="max-h-[380px] overflow-y-auto rounded border border-[#2B3547] bg-[#0B0E14]" aria-label="이미지별 검사 결과">
              {visibleRows.length === 0 && <p className="p-3 text-xs text-slate-400">이 필터에 해당하는 이미지가 없습니다.</p>}
              {visibleRows.map((row) => (
                <button
                  type="button" key={row.image.file_path}
                  onClick={() => setSelectedPath(row.image.file_path)}
                  className={`flex w-full items-center gap-2 border-b border-[#243043] px-2 py-1.5 text-left hover:bg-[#1A212E] ${selected?.image.file_path === row.image.file_path ? 'bg-[#1A212E]' : ''}`}
                >
                  <img src={resolveApiUrl(row.image.thumbnail_url)} alt="" className="h-9 w-12 shrink-0 rounded object-contain bg-black" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs text-slate-100" title={row.image.file_path}>{row.image.file_name}</span>
                    <span className="block truncate text-[10px] text-slate-400">{row.image.split} · {row.error || row.result?.rejection_reason || row.image.file_path}</span>
                  </span>
                  <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${stateColors[row.state]}`}>{stateNames[row.state]}</span>
                </button>
              ))}
            </div>
            <div className="min-h-[260px] rounded border border-[#2B3547] bg-[#0B0E14] p-3">
              {selected ? (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                    <div className="flex min-w-0 items-center gap-2"><ImageIcon className="h-4 w-4 shrink-0 text-cyan-400" /><strong className="truncate text-slate-100" title={selected.image.file_path}>{selected.image.file_name}</strong></div>
                    <span className={`${stateColors[selected.state]}`}>{stateNames[selected.state]}</span>
                  </div>
                  <p className="break-all text-[10px] text-slate-400">원본: {selected.image.file_path}</p>
                  <div className="flex h-48 items-center justify-center overflow-hidden rounded border border-[#243043] bg-black">
                    {selected.result?.annotated_image || selected.image.thumbnail_url
                      ? <img src={resolveApiUrl(selected.result?.annotated_image || selected.image.thumbnail_url)} alt={`${selected.image.file_name} 검사 미리보기`} className="h-full w-full object-contain" />
                      : <span className="text-xs text-slate-500">미리보기가 없습니다.</span>}
                  </div>
                  {selected.error && <div role="alert" className="rounded border border-red-700 bg-red-950/30 p-2 text-xs text-red-200">{selected.error}</div>}
                  {selected.state === 'skipped' && <p className="text-xs text-slate-400">이 이미지는 검사되지 않았습니다. 판정 결과가 없습니다.</p>}
                  {selected.result && (
                    <>
                      <div className="flex flex-wrap gap-3 text-xs text-slate-300">
                        <span>판정 <strong className={stateColors[selected.state]}>{selected.result.final_verdict}</strong></span>
                        <span>ROI {selected.result.roi_count}</span>
                        <span>결함 ROI {selected.result.defective_roi_count}</span>
                        <span>모델 검사 {selected.result.total_latency_ms.toFixed(1)}ms</span>
                      </div>
                      <div>
                        <h4 className="mb-1 text-xs font-semibold text-slate-200">중간 노드 결과</h4>
                        <div className="max-h-40 space-y-1 overflow-y-auto">
                          {selected.result.execution_steps.map((step, index) => (
                            <div key={`${step.node_id}-${index}`} className="flex items-center justify-between rounded border border-[#243043] px-2 py-1 text-[11px] text-slate-300">
                              <span className="truncate" title={step.node_id}>{step.name || step.node_id}</span>
                              <span className="shrink-0 font-mono">{step.status} · {Number(step.latency_ms).toFixed(1)}ms</span>
                            </div>
                          ))}
                        </div>
                      </div>
                      {selected.result.crops.length > 0 && (
                        <div>
                          <h4 className="mb-1 text-xs font-semibold text-slate-200">검사 영역 ({selected.result.crops.length})</h4>
                          <div className="flex max-h-36 gap-2 overflow-auto">
                            {selected.result.crops.map((crop) => (
                              <div key={crop.roi_id} className="w-24 shrink-0 rounded border border-[#243043] p-1 text-[10px] text-slate-300">
                                {crop.crop_thumbnail && <img src={resolveApiUrl(crop.crop_thumbnail)} alt={crop.label} className="h-16 w-full object-contain" />}
                                <div className="truncate">{crop.label}</div><div>{crop.verdict} · {Number(crop.defect_score).toFixed(2)}</div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </>
                  )}
                </div>
              ) : <p className="py-24 text-center text-xs text-slate-400">이미지를 선택하면 검사 근거를 볼 수 있습니다.</p>}
            </div>
          </div>
        </>
      )}
    </section>
  );
};

export default BatchInspectionPanel;
