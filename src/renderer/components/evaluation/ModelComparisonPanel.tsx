import React, { useEffect, useRef, useState } from 'react';
import { ArrowRight, GitCompareArrows, Loader2, RefreshCw, ShieldCheck } from 'lucide-react';
import type { Language, VisionTask } from '../../types';
import {
  api,
  type ModelComparisonModel,
  type ModelComparisonRecord,
  type ModelComparisonReport,
  type ModelComparisonOutcome,
} from '../../services/api';

interface Props {
  projectDir: string | null;
  sourceFolder: string;
  task: VisionTask;
  preferredJobId?: string | null;
  language: Language;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (error && typeof error === 'object' && 'detail' in error) return String(error.detail);
  return String(error);
}

function verdictStyle(verdict: ModelComparisonOutcome['verdict']): string {
  if (verdict === 'OK') return 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300';
  if (verdict === 'NG') return 'border-rose-500/40 bg-rose-500/10 text-rose-300';
  return 'border-amber-500/40 bg-amber-500/10 text-amber-200';
}

function Verdict({ outcome }: { outcome: ModelComparisonOutcome }) {
  return (
    <span className={`inline-flex rounded border px-1.5 py-0.5 font-mono text-[10px] font-semibold ${verdictStyle(outcome.verdict)}`}
      title={outcome.error || outcome.reason || undefined}>
      {outcome.verdict || 'ERROR'}
    </span>
  );
}

export const ModelComparisonPanel: React.FC<Props> = ({ projectDir, sourceFolder, task, preferredJobId, language }) => {
  const isKo = language === 'ko';
  const scopeKey = `${projectDir || ''}\0${sourceFolder}\0${task}`;
  const currentScope = useRef(scopeKey);
  currentScope.current = scopeKey;
  const [models, setModels] = useState<ModelComparisonModel[]>([]);
  const [records, setRecords] = useState<ModelComparisonRecord[]>([]);
  const [incumbentId, setIncumbentId] = useState('');
  const [candidateId, setCandidateId] = useState('');
  const [maxImages, setMaxImages] = useState(task === 'segmentation' ? 1 : 4);
  const [report, setReport] = useState<ModelComparisonReport | null>(null);
  const [reportScope, setReportScope] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setModels([]);
    setRecords([]);
    setIncumbentId('');
    setCandidateId('');
    setReport(null);
    setReportScope('');
    setError(null);
    setIsLoading(false);
    setIsRunning(false);
    setMaxImages(task === 'segmentation' ? 1 : 4);
    if (!projectDir || !sourceFolder) return () => { active = false; };
    setIsLoading(true);
    Promise.all([
      api.evaluation.comparisonModels(sourceFolder, task),
      api.evaluation.listComparisons(sourceFolder, task),
    ]).then(([catalog, history]) => {
      if (!active || currentScope.current !== scopeKey) return;
      setModels(catalog.models);
      setRecords(history.comparisons);
      const initial = catalog.models.find((model) => model.job_id === preferredJobId)?.job_id
        || catalog.models[0]?.job_id || '';
      setIncumbentId(initial);
      setCandidateId(catalog.models.find((model) => model.job_id !== initial)?.job_id || '');
    }).catch((cause) => {
      if (active && currentScope.current === scopeKey) setError(errorMessage(cause));
    }).finally(() => {
      if (active && currentScope.current === scopeKey) setIsLoading(false);
    });
    return () => { active = false; };
  }, [scopeKey, projectDir, sourceFolder, task]);

  const openReport = async (comparisonId: string) => {
    if (!comparisonId || !sourceFolder) return;
    const requestedScope = scopeKey;
    setIsLoading(true);
    setError(null);
    try {
      const saved = await api.evaluation.getComparison(comparisonId, sourceFolder, task);
      if (currentScope.current === requestedScope) {
        setReport(saved);
        setReportScope(requestedScope);
      }
    } catch (cause) {
      if (currentScope.current === requestedScope) setError(errorMessage(cause));
    } finally {
      if (currentScope.current === requestedScope) setIsLoading(false);
    }
  };

  const runComparison = async () => {
    if (!sourceFolder || !incumbentId || !candidateId || incumbentId === candidateId) return;
    const requestedScope = scopeKey;
    setIsRunning(true);
    setError(null);
    try {
      const created = await api.evaluation.createComparison({
        source_dataset_path: sourceFolder,
        task,
        incumbent_job_id: incumbentId,
        candidate_job_id: candidateId,
        max_images: maxImages,
      });
      if (currentScope.current !== requestedScope) return;
      setReport(created);
      setReportScope(requestedScope);
      try {
        const history = await api.evaluation.listComparisons(sourceFolder, task);
        if (currentScope.current === requestedScope) setRecords(history.comparisons);
      } catch {
        // The created report is already saved and visible. History can reload
        // the next time this stage opens, without obscuring that result.
      }
    } catch (cause) {
      if (currentScope.current === requestedScope) setError(errorMessage(cause));
    } finally {
      if (currentScope.current === requestedScope) setIsRunning(false);
    }
  };

  const visibleReport = reportScope === scopeKey ? report : null;
  const differentTrainingData = visibleReport
    && visibleReport.incumbent_training_dataset_fingerprint !== visibleReport.candidate_training_dataset_fingerprint;

  return (
    <section className="rounded-lg border border-sky-500/30 bg-gradient-to-br from-[#162636] to-[#15202d] p-3.5 text-xs text-slate-200"
      aria-label={isKo ? '현행과 후보 모델 비교' : 'Baseline and candidate model comparison'} aria-busy={isRunning}>
      <div className="flex items-start gap-2.5">
        <div className="rounded-md border border-sky-400/30 bg-sky-500/10 p-1.5 text-sky-300">
          <GitCompareArrows className="h-4 w-4" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold text-slate-50">{isKo ? '현행 모델 · 후보 모델 비교' : 'Baseline · candidate comparison'}</h3>
          <p className="mt-0.5 leading-relaxed text-slate-400">
            {isKo
              ? '같은 test 이미지에 두 완료 모델을 실행하고 원판정과 불일치를 저장합니다.'
              : 'Run two completed models on the same test images and save raw verdicts and disagreements.'}
          </p>
        </div>
      </div>

      {!projectDir || !sourceFolder ? (
        <p className="mt-3 rounded border border-amber-500/30 bg-amber-500/10 p-2 text-amber-200">
          {isKo ? '1단계에서 이 프로젝트의 데이터 출처를 선택해 주세요.' : 'Select this project’s source dataset in Stage 1.'}
        </p>
      ) : (
        <>
          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
            <label className="min-w-0 space-y-1 text-[11px] text-slate-300">
              <span>{isKo ? '비교 기준 모델 (수동 선택)' : 'Baseline model (manual)'}</span>
              <select aria-label={isKo ? '비교 기준 모델' : 'Baseline model'} value={incumbentId}
                onChange={(event) => setIncumbentId(event.target.value)} disabled={isRunning || isLoading}
                className="w-full rounded border border-[#3D5266] bg-[#0F1B27] px-2 py-1.5 text-slate-100">
                <option value="">{isKo ? '모델 선택' : 'Select model'}</option>
                {models.map((model) => <option key={model.job_id} value={model.job_id}>{model.job_id}</option>)}
              </select>
            </label>
            <label className="min-w-0 space-y-1 text-[11px] text-slate-300">
              <span>{isKo ? '후보 모델' : 'Candidate model'}</span>
              <select aria-label={isKo ? '후보 모델' : 'Candidate model'} value={candidateId}
                onChange={(event) => setCandidateId(event.target.value)} disabled={isRunning || isLoading}
                className="w-full rounded border border-[#3D5266] bg-[#0F1B27] px-2 py-1.5 text-slate-100">
                <option value="">{isKo ? '모델 선택' : 'Select model'}</option>
                {models.map((model) => <option key={model.job_id} value={model.job_id}>{model.job_id}</option>)}
              </select>
            </label>
          </div>
          <div className="mt-2 flex flex-wrap items-end gap-2">
            <label className="space-y-1 text-[11px] text-slate-300">
              <span>{isKo ? 'test 이미지 수' : 'Test images'}</span>
              <select aria-label={isKo ? 'test 이미지 수' : 'Test image count'} value={maxImages}
                onChange={(event) => setMaxImages(Number(event.target.value))} disabled={isRunning}
                className="block rounded border border-[#3D5266] bg-[#0F1B27] px-2 py-1.5 text-slate-100">
                {[1, 4, 8, 16].map((count) => <option key={count} value={count}>{count}</option>)}
              </select>
            </label>
            <button type="button" onClick={() => void runComparison()}
              disabled={isRunning || isLoading || !incumbentId || !candidateId || incumbentId === candidateId}
              className="inline-flex min-h-8 items-center justify-center gap-1.5 rounded border border-sky-400/50 bg-sky-600 px-3 py-1.5 font-semibold text-white transition-colors hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-40">
              {isRunning ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                : <GitCompareArrows className="h-3.5 w-3.5" aria-hidden="true" />}
              {isKo ? (isRunning ? '비교 실행 중…' : '동일 test 이미지로 비교') : (isRunning ? 'Comparing…' : 'Compare same test images')}
            </button>
          </div>
          <p className="mt-2 text-[10px] leading-relaxed text-slate-400">
            {isKo
              ? '선택한 수까지 test 목록 앞에서부터 사용하며 실제 이미지 수·경로·SHA-256을 기록합니다. 활성 모델은 변경하지 않습니다.'
              : 'Uses the first selected test images and records exact paths and SHA-256 hashes. The active model is unchanged.'}
            {task === 'segmentation' && (
              <span className="block text-amber-200/85">
                {isKo ? '원본 해상도 타일 검사이므로 고해상도 이미지는 시간이 걸릴 수 있습니다.' : 'Original-resolution tiled inspection may take time on large images.'}
              </span>
            )}
          </p>
          {models.length < 2 && !isLoading && (
            <p className="mt-2 rounded border border-amber-500/30 bg-amber-500/10 p-2 text-amber-200">
              {isKo ? '이 프로젝트·출처·작업 유형에 완료된 모델 2개가 필요합니다.' : 'Two completed models from this project, source, and task are required.'}
            </p>
          )}

          {records.length > 0 && (
            <label className="mt-3 block space-y-1 border-t border-slate-600/50 pt-2 text-[11px] text-slate-300">
              <span>{isKo ? '저장된 비교 다시 열기' : 'Reopen saved comparison'}</span>
              <select aria-label={isKo ? '저장된 모델 비교' : 'Saved model comparison'}
                value={visibleReport?.comparison_id || ''} onChange={(event) => void openReport(event.target.value)}
                disabled={isLoading || isRunning}
                className="w-full rounded border border-[#3D5266] bg-[#0F1B27] px-2 py-1.5 text-slate-100">
                <option value="">{isKo ? '비교 기록 선택' : 'Select saved comparison'}</option>
                {records.map((record) => (
                  <option key={record.comparison_id} value={record.comparison_id}>
                    {record.created_at.slice(0, 16).replace('T', ' ')} · {record.incumbent_job_id} → {record.candidate_job_id}
                  </option>
                ))}
              </select>
            </label>
          )}
        </>
      )}

      {isLoading && <p className="mt-2 inline-flex items-center gap-1.5 text-sky-200"><RefreshCw className="h-3 w-3 animate-spin" />{isKo ? '목록 불러오는 중…' : 'Loading…'}</p>}
      {error && <p role="alert" className="mt-2 rounded border border-rose-500/40 bg-rose-950/30 p-2 text-rose-200">{error}</p>}

      {visibleReport && (
        <div className="mt-3 space-y-2.5 border-t border-sky-400/20 pt-3">
          <div className="flex flex-wrap items-center gap-1.5">
            <ShieldCheck className="h-3.5 w-3.5 text-sky-300" aria-hidden="true" />
            <span className="font-semibold text-slate-100">{isKo ? '저장된 비교 근거' : 'Saved comparison evidence'}</span>
            <span className="ml-auto font-mono text-[10px] text-slate-400">{visibleReport.created_at.slice(0, 19).replace('T', ' ')}</span>
          </div>
          <p className="break-all font-mono text-[10px] text-slate-400">{visibleReport.incumbent_job_id} → {visibleReport.candidate_job_id}</p>
          <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
            {[
              [isKo ? 'test 표본' : 'Test sample', `${visibleReport.selected_image_count}/${visibleReport.total_test_images}`],
              [isKo ? '판정 불일치' : 'Disagree', String(visibleReport.summary.disagreements)],
              ['NG → OK', String(visibleReport.summary.ng_to_ok)],
              ['OK → NG', String(visibleReport.summary.ok_to_ng)],
            ].map(([label, value]) => (
              <div key={label} className="rounded border border-slate-600/50 bg-slate-950/30 px-2 py-1.5">
                <div className="text-[10px] text-slate-400">{label}</div>
                <div className="font-mono text-base font-bold tabular-nums text-slate-100">{value}</div>
              </div>
            ))}
          </div>
          <p className="text-[10px] text-slate-400">
            {isKo ? '비교 가능' : 'Comparable'} {visibleReport.summary.comparable_images}
            {' · '}{isKo ? '실행 오류' : 'Errors'} {visibleReport.summary.error_images}
            {' · '}{isKo ? '정답 미확인' : 'Unknown truth'} {visibleReport.summary.unknown_truth_images}
          </p>
          {visibleReport.summary.new_missed_ng > 0 && (
            <p role="alert" className="rounded border border-rose-500/50 bg-rose-950/30 p-2 text-[11px] font-semibold text-rose-200">
              {isKo
                ? `NG 정답 중 기준은 NG, 후보는 OK로 판정한 이미지 ${visibleReport.summary.new_missed_ng}장 — 개별 사례 확인이 필요합니다.`
                : `${visibleReport.summary.new_missed_ng} known NG image(s) changed from baseline NG to candidate OK. Review each case.`}
            </p>
          )}
          {visibleReport.summary.new_overkill_ok > 0 && (
            <p className="rounded border border-amber-500/40 bg-amber-950/25 p-2 text-[11px] text-amber-200">
              {isKo
                ? `OK 정답 중 기준은 OK, 후보는 NG로 판정한 이미지 ${visibleReport.summary.new_overkill_ok}장입니다.`
                : `${visibleReport.summary.new_overkill_ok} known OK image(s) changed from baseline OK to candidate NG.`}
            </p>
          )}
          {(visibleReport.summary.error_images > 0 || visibleReport.summary.known_ok_images === 0 || differentTrainingData) && (
            <div className="rounded border border-amber-500/40 bg-amber-950/25 p-2 text-[11px] leading-relaxed text-amber-200">
              {visibleReport.summary.error_images > 0 && <p>{isKo ? `실행 오류 ${visibleReport.summary.error_images}건은 비교 가능 표본에서 제외했습니다.` : `${visibleReport.summary.error_images} errored images were excluded from comparisons.`}</p>}
              {visibleReport.summary.known_ok_images === 0 && <p>{isKo ? 'OK 정답 이미지가 없어 과검률을 판단할 수 없습니다.' : 'No known OK images: false-positive rate cannot be assessed.'}</p>}
              {differentTrainingData && <p>{isKo ? '두 모델의 학습 데이터 버전이 다릅니다. 아래 fingerprint를 확인하세요.' : 'The models were trained on different dataset versions; review fingerprints below.'}</p>}
            </div>
          )}
          <div className="space-y-1.5" aria-label={isKo ? '이미지별 원판정' : 'Per-image raw verdicts'}>
            {visibleReport.images.map((row) => (
              <div key={`${row.file_path}-${row.image_sha256}`} className={`rounded border p-2 ${
                row.ground_truth_verdict === 'NG' && row.incumbent.verdict === 'NG' && row.candidate.verdict === 'OK'
                  ? 'border-rose-500/60 bg-rose-500/10'
                  : row.disagrees ? 'border-amber-500/45 bg-amber-500/5' : 'border-slate-600/50 bg-slate-950/25'
              }`}>
                <div className="flex min-w-0 items-center gap-2">
                  <span className="min-w-0 truncate font-medium text-slate-100" title={row.file_path}>{row.file_name}</span>
                  <span className="ml-auto shrink-0 text-[10px] text-slate-400">{isKo ? '정답' : 'Truth'} {row.ground_truth_verdict || '?'}</span>
                  {row.ground_truth_verdict === 'NG' && row.incumbent.verdict === 'NG' && row.candidate.verdict === 'OK' && (
                    <span className="shrink-0 rounded bg-rose-500/25 px-1 py-0.5 text-[10px] font-bold text-rose-200">
                      {isKo ? 'NG 미검 후보' : 'Possible miss'}
                    </span>
                  )}
                  {row.disagrees && <span className="shrink-0 rounded bg-amber-500/20 px-1 py-0.5 text-[10px] font-bold text-amber-200">{isKo ? '불일치' : 'Changed'}</span>}
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  <span className="text-[10px] text-slate-400">{isKo ? '기준' : 'Base'}</span><Verdict outcome={row.incumbent} />
                  <ArrowRight className="h-3 w-3 text-slate-500" aria-hidden="true" />
                  <span className="text-[10px] text-slate-400">{isKo ? '후보' : 'Candidate'}</span><Verdict outcome={row.candidate} />
                  <span className="ml-auto font-mono text-[10px] text-slate-500" title={row.image_sha256}>SHA {row.image_sha256.slice(0, 10)}</span>
                </div>
                {(row.incumbent.error || row.candidate.error) && (
                  <p className="mt-1 break-words text-[10px] text-rose-300">{row.incumbent.error || row.candidate.error}</p>
                )}
                <details className="mt-1.5 text-[10px] text-slate-400">
                  <summary className="cursor-pointer text-sky-300">{isKo ? '이미지·모델 판정 근거' : 'Image and verdict evidence'}</summary>
                  <div className="mt-1 space-y-1 break-all">
                    <p>path: <code>{row.file_path}</code></p>
                    <p>image SHA-256: <code>{row.image_sha256}</code></p>
                    <p>{isKo ? '기준 결함 점수' : 'Baseline defect score'}: {row.incumbent.max_defect_score ?? '—'} · {row.incumbent.reason || '—'}</p>
                    <p>{isKo ? '후보 결함 점수' : 'Candidate defect score'}: {row.candidate.max_defect_score ?? '—'} · {row.candidate.reason || '—'}</p>
                  </div>
                </details>
              </div>
            ))}
          </div>
          <details className="rounded border border-slate-600/50 bg-slate-950/20 p-2 text-[10px] text-slate-400">
            <summary className="cursor-pointer font-medium text-slate-300">{isKo ? '재현 정보와 해석 범위' : 'Provenance and interpretation'}</summary>
            <dl className="mt-2 space-y-1 break-all font-mono">
              <div><dt className="text-slate-500">dataset fingerprint</dt><dd>{visibleReport.dataset_fingerprint}</dd></div>
              <div><dt className="text-slate-500">baseline training fingerprint</dt><dd>{visibleReport.incumbent_training_dataset_fingerprint}</dd></div>
              <div><dt className="text-slate-500">candidate training fingerprint</dt><dd>{visibleReport.candidate_training_dataset_fingerprint}</dd></div>
              <div><dt className="text-slate-500">baseline checkpoint SHA-256</dt><dd>{visibleReport.model_sha256.incumbent}</dd></div>
              <div><dt className="text-slate-500">candidate checkpoint SHA-256</dt><dd>{visibleReport.model_sha256.candidate}</dd></div>
              <div><dt className="text-slate-500">report ID</dt><dd>{visibleReport.comparison_id}</dd></div>
            </dl>
            <ul className="mt-2 list-inside list-disc space-y-1 font-sans text-[11px] text-slate-400">
              {visibleReport.limitations.map((limit) => <li key={limit}>{limit}</li>)}
            </ul>
          </details>
        </div>
      )}
    </section>
  );
};
