import React, { useEffect, useState } from 'react';
import { Crosshair, Loader2, RefreshCw, Square } from 'lucide-react';
import {
  api, type RotatedEvaluation, type RotatedJob, type RotatedModelSummary,
  type RotatedPrediction, type RotatedSampleRow,
} from '../../services/api';
import { useProjectStore } from '../../stores/useProjectStore';

function parseRows(text: string): RotatedSampleRow[] {
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (!lines.length) throw new Error('회전 박스 정답을 한 줄 이상 입력하세요.');
  return lines.map((line, index) => {
    const [image, label, coordinates, split, ...extra] = line.split('\t');
    const values = coordinates?.split(',').map((value) => Number(value.trim())) ?? [];
    if (!image?.trim() || !label?.trim() || extra.length || values.length !== 5 ||
        values.some((value) => !Number.isFinite(value)) || !['train', 'val', 'test'].includes(split)) {
      throw new Error(`${index + 1}행은 이미지 상대 경로, 라벨, cx,cy,너비,높이,각도, train/val/test를 탭으로 나누세요.`);
    }
    const [cx, cy, width, height, angle_deg] = values;
    if (width <= 0 || height <= 0 || angle_deg < -90 || angle_deg >= 90) {
      throw new Error(`${index + 1}행의 너비·높이는 양수, 각도는 -90° 이상 90° 미만이어야 합니다.`);
    }
    return {
      image: image.trim(), label: label.trim(), split: split as RotatedSampleRow['split'],
      box: { cx, cy, width, height, angle_deg },
    };
  });
}

function formatRows(rows: RotatedSampleRow[]): string {
  return rows.map((row) => `${row.image}\t${row.label}\t${[
    row.box.cx, row.box.cy, row.box.width, row.box.height, row.box.angle_deg,
  ].join(',')}\t${row.split}`).join('\n');
}

function errorText(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

export const RotatedDetectionPanel: React.FC = () => {
  const projectDir = useProjectStore((state) => state.projectDir);
  const datasetPath = useProjectStore((state) => state.project?.source_dataset_dir || '');
  const [rowsText, setRowsText] = useState('');
  const [sampleCount, setSampleCount] = useState<number | null>(null);
  const [splitCounts, setSplitCounts] = useState<Record<string, number> | null>(null);
  const [epochs, setEpochs] = useState(10);
  const [models, setModels] = useState<RotatedModelSummary[]>([]);
  const [modelId, setModelId] = useState('');
  const [job, setJob] = useState<RotatedJob | null>(null);
  const [imagePath, setImagePath] = useState('');
  const [evaluation, setEvaluation] = useState<RotatedEvaluation | null>(null);
  const [prediction, setPrediction] = useState<RotatedPrediction | null>(null);
  const [busy, setBusy] = useState<'manifest' | 'train' | 'cancel' | 'evaluate' | 'predict' | null>(null);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const isActive = job?.status === 'running' || job?.status === 'stopping';

  const sameProject = () => useProjectStore.getState().projectDir === projectDir &&
    (useProjectStore.getState().project?.source_dataset_dir || '') === datasetPath;

  useEffect(() => {
    setRowsText(''); setSampleCount(null); setSplitCounts(null);
    setModels([]); setModelId(''); setJob(null); setImagePath('');
    setEvaluation(null); setPrediction(null); setNotice(''); setError('');
    if (!projectDir) return;
    let active = true;
    void api.rotated.models().then(({ models: items }) => {
      if (!active || !sameProject()) return;
      setModels(items); setModelId(items[0]?.job_id || '');
    }).catch((cause) => { if (active && sameProject()) setError(errorText(cause)); });
    if (datasetPath) void api.rotated.manifest(datasetPath).then((manifest) => {
      if (!active || !sameProject()) return;
      setRowsText(formatRows(manifest.samples));
      setSampleCount(manifest.sample_count);
      setSplitCounts(manifest.split_counts);
      const test = manifest.samples.find((row) => row.split === 'test');
      if (test) setImagePath(`${datasetPath.replace(/\/+$/, '')}/${test.image}`);
    }).catch(() => {
      // A newly selected source may have no rotated labels yet.
    });
    return () => { active = false; };
  }, [projectDir, datasetPath]);

  useEffect(() => {
    if (!job || !isActive || !projectDir) return;
    let active = true;
    const check = async () => {
      try {
        const status = await api.rotated.job(job.job_id);
        if (!active || !sameProject()) return;
        setJob(status);
        if (status.status === 'completed') {
          const refreshed = await api.rotated.models();
          if (!active || !sameProject()) return;
          setModels(refreshed.models); setModelId(status.job_id);
          setNotice(`회전 박스 후보 학습 완료 · ${status.job_id.slice(0, 8)}. 시험 분할 평가와 이미지 확인이 필요합니다.`);
        } else if (status.status === 'aborted') {
          setNotice('회전 박스 학습을 취소했습니다. 후보 모델은 등록되지 않았습니다.');
        } else if (status.status === 'failed') {
          setError(status.error || '회전 박스 학습이 실패했습니다.');
        }
      } catch (cause) {
        if (active && sameProject()) setError(errorText(cause));
      }
    };
    const timer = window.setInterval(() => void check(), 700);
    void check();
    return () => { active = false; window.clearInterval(timer); };
  }, [job?.job_id, job?.status, projectDir, datasetPath]);

  const applyManifest = (result: {
    sample_count: number; split_counts: Record<string, number>; samples: RotatedSampleRow[];
  }) => {
    setRowsText(formatRows(result.samples));
    setSampleCount(result.sample_count);
    setSplitCounts(result.split_counts);
    const test = result.samples.find((row) => row.split === 'test');
    if (test && datasetPath) setImagePath(`${datasetPath.replace(/\/+$/, '')}/${test.image}`);
  };

  const loadManifest = async () => {
    if (!datasetPath || busy) return;
    setBusy('manifest'); setError(''); setNotice('');
    try {
      const result = await api.rotated.manifest(datasetPath);
      if (!sameProject()) return;
      applyManifest(result);
      setNotice(`${result.sample_count}개 회전 박스와 원본 이미지 해시를 확인했습니다.`);
    } catch (cause) { if (sameProject()) setError(errorText(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };

  const saveManifest = async () => {
    if (!datasetPath || busy) return;
    let rows: RotatedSampleRow[];
    try { rows = parseRows(rowsText); }
    catch (cause) { setError(errorText(cause)); return; }
    setBusy('manifest'); setError(''); setNotice('');
    try {
      const result = await api.rotated.saveManifest(datasetPath, rows);
      if (!sameProject()) return;
      applyManifest(result);
      setNotice(`${result.sample_count}개 회전 박스를 원본 해시와 함께 저장했습니다.`);
    } catch (cause) { if (sameProject()) setError(errorText(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };

  const startTraining = async () => {
    if (!datasetPath || !sampleCount || busy || isActive) return;
    setBusy('train'); setError(''); setNotice(''); setEvaluation(null); setPrediction(null);
    try {
      const started = await api.rotated.train(datasetPath, epochs);
      if (!sameProject()) return;
      setJob(started);
      if (started.status === 'completed') {
        const refreshed = await api.rotated.models();
        if (!sameProject()) return;
        setModels(refreshed.models); setModelId(started.job_id);
        setNotice(`회전 박스 후보 학습 완료 · ${started.job_id.slice(0, 8)}. 시험 분할 평가와 이미지 확인이 필요합니다.`);
      } else if (started.status === 'aborted') {
        setNotice('회전 박스 학습을 취소했습니다. 후보 모델은 등록되지 않았습니다.');
      } else if (started.status === 'failed') {
        setError(started.error || '회전 박스 학습이 실패했습니다.');
      } else {
        setNotice(`학습 작업 ${started.job_id.slice(0, 8)}을 시작했습니다. 완료 전에는 후보로 선택되지 않습니다.`);
      }
    } catch (cause) { if (sameProject()) setError(errorText(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };

  const cancelTraining = async () => {
    if (!job || job.status !== 'running' || busy) return;
    setBusy('cancel'); setError('');
    try {
      const stopped = await api.rotated.cancel(job.job_id);
      if (sameProject()) setJob(stopped);
    } catch (cause) { if (sameProject()) setError(errorText(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };

  const evaluate = async () => {
    if (!modelId || !datasetPath || busy) return;
    setBusy('evaluate'); setError(''); setEvaluation(null);
    try {
      const result = await api.rotated.evaluate(modelId, datasetPath);
      if (sameProject()) setEvaluation(result);
    } catch (cause) { if (sameProject()) setError(errorText(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };

  const predict = async () => {
    if (!modelId || !imagePath.trim() || busy) return;
    setBusy('predict'); setError(''); setPrediction(null);
    try {
      const result = await api.rotated.predict(modelId, imagePath.trim());
      if (sameProject()) setPrediction(result);
    } catch (cause) { if (sameProject()) setError(errorText(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };

  return <details className="rounded-xl border border-[#344255] bg-[#141D2B] text-xs text-slate-200">
    <summary className="flex cursor-pointer items-center gap-2 px-4 py-3 font-semibold text-slate-100">
      <Crosshair className="h-4 w-4 text-amber-400" /> 회전 객체 위치 모델
      <span className="font-normal text-slate-400">한 크롭에 객체 하나 · 방향과 위치 예측</span>
    </summary>
    <div className="space-y-4 border-t border-[#344255] p-4">
      <p className="leading-5 text-slate-400">원본 이미지의 회전 박스 정답을 지정해 후보 모델을 학습합니다. 여러 객체 검출과 분류, 5단계 플로우 자동 적용은 아직 지원하지 않습니다.</p>
      <div className="rounded border border-[#344255] bg-[#0E1722] px-3 py-2">
        <div className="text-slate-400">현재 프로젝트 원본 폴더</div>
        <div className="mt-1 break-all font-mono text-slate-200">{datasetPath || '1단계에서 원본 이미지 폴더를 먼저 선택하세요.'}</div>
      </div>
      <label className="block text-slate-300">정답 표 · 이미지 상대 경로 ↹ 라벨 ↹ cx,cy,너비,높이,각도 ↹ train/val/test
        <textarea value={rowsText} onChange={(event) => { setRowsText(event.target.value); setSampleCount(null); setSplitCounts(null); }} rows={5}
          placeholder={'images/part_001.png\tdefect\t42,30,18,9,25\ttrain\nimages/part_002.png\tdefect\t40,31,19,8,-12\tval\nimages/part_003.png\tdefect\t44,29,17,7,10\ttest'}
          className="mt-1 w-full rounded border border-slate-600 bg-[#0E1722] px-3 py-2 font-mono text-slate-100" />
      </label>
      <p className="text-slate-500">좌표는 이미지 원본 픽셀입니다. 각도는 화면에서 시계 방향이며 -90° 이상 90° 미만입니다. 동일 이미지 또는 동일 바이트의 복사본은 서로 다른 분할에 둘 수 없습니다.</p>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => void loadManifest()} disabled={!datasetPath || !!busy}
          className="rounded border border-slate-600 px-3 py-1.5 hover:bg-slate-700 disabled:opacity-40"><RefreshCw className="mr-1 inline h-3 w-3" />저장된 정답 읽기</button>
        <button type="button" onClick={() => void saveManifest()} disabled={!datasetPath || !rowsText.trim() || !!busy || isActive}
          className="rounded border border-amber-700 bg-amber-950/40 px-3 py-1.5 text-amber-200 hover:bg-amber-900/40 disabled:opacity-40">정답·이미지 해시 저장</button>
        {sampleCount !== null && splitCounts && <span className="text-emerald-300">검증 {sampleCount}개 · 학습 {splitCounts.train || 0} / 검증 {splitCounts.val || 0} / 시험 {splitCounts.test || 0}</span>}
      </div>
      <div className="flex flex-wrap items-end gap-2 border-t border-[#344255] pt-4">
        <label>학습 epoch<input type="number" min="1" max="200" value={epochs}
          onChange={(event) => setEpochs(Math.max(1, Math.min(200, Number(event.target.value) || 1)))}
          className="mt-1 block w-20 rounded border border-slate-600 bg-[#0E1722] px-2 py-1.5" /></label>
        <button type="button" onClick={() => void startTraining()} disabled={!sampleCount || !!busy || isActive}
          className="rounded bg-amber-700 px-3 py-2 font-semibold text-white hover:bg-amber-600 disabled:opacity-40">후보 학습</button>
        {isActive && <button type="button" onClick={() => void cancelTraining()} disabled={job?.status !== 'running' || !!busy}
          className="rounded border border-rose-700 px-3 py-2 text-rose-200 hover:bg-rose-950 disabled:opacity-40"><Square className="mr-1 inline h-3 w-3" />취소</button>}
        <label className="min-w-[220px] flex-1">완료 후보 모델
          <select value={modelId} onChange={(event) => { setModelId(event.target.value); setEvaluation(null); setPrediction(null); }}
            className="mt-1 block w-full rounded border border-slate-600 bg-[#0E1722] px-2 py-1.5">
            {!models.length && <option value="">완료 모델 없음</option>}
            {models.map((model) => <option key={model.job_id} value={model.job_id}>{model.job_id.slice(0, 12)} · 검증 IoU {(model.validation.mean_oriented_iou * 100).toFixed(1)}%</option>)}
          </select>
        </label>
        <button type="button" onClick={() => void evaluate()} disabled={!modelId || !datasetPath || !!busy || !splitCounts?.test}
          className="rounded border border-slate-600 px-3 py-2 hover:bg-slate-700 disabled:opacity-40">시험 분할 평가</button>
      </div>
      {job && isActive && <p role="status" className="text-amber-200"><Loader2 className="mr-1 inline h-3 w-3 animate-spin" />{job.status === 'stopping' ? '학습을 멈추는 중' : '로컬 CPU에서 학습 중'} · 작업 {job.job_id.slice(0, 8)}</p>}
      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-[260px] flex-1">한 장 시험 이미지 경로
          <input value={imagePath} onChange={(event) => setImagePath(event.target.value)} placeholder="원본 폴더 안의 시험 이미지 절대 경로"
            className="mt-1 block w-full rounded border border-slate-600 bg-[#0E1722] px-3 py-1.5 font-mono" />
        </label>
        <button type="button" onClick={() => void predict()} disabled={!modelId || !imagePath.trim() || !!busy}
          className="rounded border border-amber-600 px-3 py-2 text-amber-200 hover:bg-amber-950 disabled:opacity-40">회전 박스 예측</button>
      </div>
      {busy && <p role="status" className="text-amber-200"><Loader2 className="mr-1 inline h-3 w-3 animate-spin" />처리 중…</p>}
      {error && <p role="alert" className="rounded border border-rose-700 bg-rose-950/30 p-2 text-rose-200">{error}</p>}
      {notice && <p role="status" className="text-emerald-300">{notice}</p>}
      {evaluation && <div className="rounded border border-[#344255] bg-[#0E1722] p-3">
        시험 {evaluation.sample_count}장 · 평균 회전 IoU {(evaluation.mean_oriented_iou * 100).toFixed(1)}% · 평균 각도 오차 {evaluation.mean_angle_error_deg.toFixed(1)}°
        <p className="mt-1 text-slate-500">시험 결과는 후보 품질 확인 자료입니다. 현장 승인이나 5단계 플로우 적용을 뜻하지 않습니다.</p>
      </div>}
      {prediction && <div className="grid gap-3 rounded border border-[#344255] bg-[#0E1722] p-3 md:grid-cols-[minmax(0,480px)_1fr]">
        <img src={prediction.preview_data_url} alt="원본 이미지 위에 예측된 회전 박스를 그린 미리보기" className="max-h-[320px] w-full rounded border border-slate-700 object-contain" />
        <div className="space-y-2">
          <div className="font-semibold text-slate-100">{prediction.label} · {prediction.image_size[0]}×{prediction.image_size[1]} 원본 픽셀</div>
          <div>중심 ({prediction.box.cx.toFixed(1)}, {prediction.box.cy.toFixed(1)})</div>
          <div>크기 {prediction.box.width.toFixed(1)} × {prediction.box.height.toFixed(1)} px</div>
          <div>회전 {prediction.box.angle_deg.toFixed(1)}°</div>
          <div className="break-all font-mono text-[10px] text-slate-500">모델 SHA-256: {prediction.model_sha256}</div>
          <div className="break-all font-mono text-[10px] text-slate-500">이미지 SHA-256: {prediction.source_sha256}</div>
          <p className="text-slate-400">한 이미지에서 박스 한 개를 예측합니다. 신뢰도 점수와 다중 객체 판정은 제공하지 않습니다.</p>
        </div>
      </div>}
    </div>
  </details>;
};
