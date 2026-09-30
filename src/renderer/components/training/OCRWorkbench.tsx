import React, { useEffect, useState } from 'react';
import { FileText, Loader2, RefreshCw } from 'lucide-react';
import { api, type OCREvaluation, type OCRLabelRow, type OCRModelSummary } from '../../services/api';
import { useProjectStore } from '../../stores/useProjectStore';
import {useSpecializedTraining} from './useSpecializedTraining';
import {SpecializedTrainingStatus} from './SpecializedTrainingStatus';
import {WarmStartSelector} from './WarmStartSelector';

function parseRows(value: string): OCRLabelRow[] {
  const rows = value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (!rows.length) throw new Error('문자 이미지와 정답 문자열을 먼저 입력하세요.');
  return rows.map((line, index) => {
    const [image, text, split, ...extra] = line.split('\t');
    if (!image?.trim() || !text?.trim() || !['train', 'val', 'test'].includes(split) || extra.length) {
      throw new Error(`${index + 1}행은 이미지 상대 경로, 정답 문자열, train/val/test를 탭으로 나누세요.`);
    }
    return { image: image.trim(), text: text.trim(), split: split as OCRLabelRow['split'] };
  });
}

function describeError(cause: unknown): string {
  if (cause instanceof Error) return cause.message;
  if (cause && typeof cause === 'object' && 'message' in cause) return String(cause.message);
  return String(cause);
}

export const OCRWorkbench: React.FC = () => {
  const projectDir = useProjectStore((state) => state.projectDir);
  const projectSource=useProjectStore(state=>state.project?.source_dataset_dir ?? '');
  const activeLabelset=useProjectStore(state=>state.project?.active_labelset_id ?? 'default');
  const [datasetPath, setDatasetPath] = useState('');
  const [rowsText, setRowsText] = useState('');
  const [epochs, setEpochs] = useState(20);
  const [warmParentId, setWarmParentId] = useState('');
  const [models, setModels] = useState<OCRModelSummary[]>([]);
  const [jobId, setJobId] = useState('');
  const [imagePath, setImagePath] = useState('');
  const [manifestCount, setManifestCount] = useState<number | null>(null);
  const [evaluation, setEvaluation] = useState<OCREvaluation | null>(null);
  const [prediction, setPrediction] = useState<{ text: string; confidence: number; model_sha256: string } | null>(null);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<'manifest' | 'train' | 'evaluate' | 'predict' | null>(null);

  const training=useSpecializedTraining('ocr',(completed)=>{
    void api.ocr.models().then(result=>{
      if(!sameProject())return;
      setModels(result.models);setJobId(completed.job_id);setNotice(`학습 완료 · ${completed.job_id.slice(0,8)}. 평가 후 후보를 검토하세요.`);
    }).catch(cause=>{if(sameProject())setError(cause.message ?? String(cause));});
  });

  useEffect(() => {
    setBusy(null);setDatasetPath(projectSource);setRowsText('');
    setManifestCount(null);setModels([]);
    setJobId('');
    setEvaluation(null);
    setPrediction(null);
    if (!projectDir) return;
    let active = true;
    void api.ocr.models().then((result) => {
      if (!active || useProjectStore.getState().projectDir !== projectDir) return;
      setModels(result.models);
      setJobId(result.models[0]?.job_id || '');
    }).catch((cause) => { if (active) setError(describeError(cause)); });
    return () => { active = false; };
  }, [projectDir,projectSource,activeLabelset]);

  const sameProject = () => {const state=useProjectStore.getState();return state.projectDir===projectDir && (state.project?.source_dataset_dir ?? '')===projectSource && (state.project?.active_labelset_id ?? 'default')===activeLabelset;};

  const loadManifest = async () => {
    if (!datasetPath.trim() || !projectDir || busy) return;
    setBusy('manifest'); setError(''); setNotice('');
    try {
      const result = await api.ocr.manifest(datasetPath.trim());
      if (!sameProject()) return;
      setRowsText(result.samples.map((row) => `${row.image}\t${row.text}\t${row.split}`).join('\n'));
      setManifestCount(result.sample_count);
      setNotice(`${result.sample_count}개 문자 정답과 원본 해시를 확인했습니다.`);
    } catch (cause) { if (sameProject()) setError(describeError(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };

  const saveManifest = async () => {
    if (!datasetPath.trim() || !projectDir || busy) return;
    let rows: OCRLabelRow[];
    try { rows = parseRows(rowsText); }
    catch (cause) { setError(describeError(cause)); return; }
    setBusy('manifest'); setError(''); setNotice('');
    try {
      const result = await api.ocr.saveManifest(datasetPath.trim(), rows);
      if (!sameProject()) return;
      setManifestCount(result.sample_count);
      setNotice(`${result.sample_count}개 문자 정답을 이미지 해시와 함께 저장했습니다.`);
    } catch (cause) { if (sameProject()) setError(describeError(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };

  const train = async () => {
    if (!datasetPath.trim() || !projectDir || busy || !manifestCount) return;
    setBusy('train'); setError(''); setNotice('');
    try {
      await training.start(datasetPath.trim(),epochs,warmParentId || undefined);
      if(sameProject())setNotice('학습 작업을 저장했습니다. 중지하거나 다시 열어 진행 상태를 확인할 수 있습니다.');
    } catch (cause) { if (sameProject()) setError(describeError(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };

  const evaluate = async () => {
    if (!datasetPath.trim() || !jobId || busy) return;
    setBusy('evaluate'); setError(''); setEvaluation(null);
    try {
      const result = await api.ocr.evaluate(jobId, datasetPath.trim());
      if (sameProject()) setEvaluation(result);
    } catch (cause) { if (sameProject()) setError(describeError(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };

  const predict = async () => {
    if (!imagePath.trim() || !jobId || busy) return;
    setBusy('predict'); setError(''); setPrediction(null);
    try {
      const result = await api.ocr.predict(jobId, imagePath.trim());
      if (sameProject()) setPrediction(result);
    } catch (cause) { if (sameProject()) setError(describeError(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };

  return <details className="rounded-xl border border-[#344255] bg-[#141D2B] text-xs text-slate-200">
    <summary className="flex cursor-pointer items-center gap-2 px-4 py-3 font-semibold text-slate-100">
      <FileText className="h-4 w-4 text-cyan-400" /> 문자 인식 모델 실험 <span className="font-normal text-slate-400">단일 행 텍스트 이미지</span>
    </summary>
    <div className="space-y-4 border-t border-[#344255] p-4">
      <p className="leading-5 text-slate-400">문자가 한 줄로 잘린 이미지와 실제 정답 문자열이 필요합니다. 후보 모델은 자동으로 검사 플로우에 적용되지 않습니다.</p>
      <label className="block text-slate-300">문자 이미지 폴더 경로
        <input value={datasetPath} onChange={(event) => { setDatasetPath(event.target.value); setManifestCount(null); }}
          placeholder="/path/to/text-crops" className="mt-1 w-full rounded border border-slate-600 bg-[#0E1722] px-3 py-2 font-mono text-slate-100" />
      </label>
      <label className="block text-slate-300">정답 표 · 한 줄에 이미지 상대 경로 ↹ 정답 문자열 ↹ train/val/test
        <textarea value={rowsText} onChange={(event) => { setRowsText(event.target.value); setManifestCount(null); }} rows={5}
          placeholder={'images/part_001.png\tABC123\ttrain\nimages/part_002.png\tABC124\tval\nimages/part_003.png\tABC125\ttest'}
          className="mt-1 w-full rounded border border-slate-600 bg-[#0E1722] px-3 py-2 font-mono text-slate-100" />
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => void loadManifest()} disabled={!datasetPath || (!!busy || training.active)} className="rounded border border-slate-600 px-3 py-1.5 hover:bg-slate-700 disabled:opacity-40"><RefreshCw className="mr-1 inline h-3 w-3" />저장된 정답 읽기</button>
        <button type="button" onClick={() => void saveManifest()} disabled={!datasetPath || !rowsText.trim() || (!!busy || training.active)} className="rounded border border-cyan-700 bg-cyan-950/40 px-3 py-1.5 text-cyan-200 hover:bg-cyan-900/40 disabled:opacity-40">정답과 이미지 해시 저장</button>
        {manifestCount !== null && <span className="text-emerald-300">검증된 정답 {manifestCount}개</span>}
      </div>
      <SpecializedTrainingStatus {...training} />
      <WarmStartSelector family="ocr" datasetPath={datasetPath} value={warmParentId} onChange={setWarmParentId} disabled={!!busy || training.active} refreshKey={training.job?.status === 'completed' ? training.job.job_id : null} />
      <div className="flex flex-wrap items-end gap-2 border-t border-[#344255] pt-4">
        <label>학습 epoch<input type="number" min="1" max="500" value={epochs} onChange={(event) => setEpochs(Math.max(1, Math.min(500, Number(event.target.value) || 1)))}
          className="mt-1 block w-20 rounded border border-slate-600 bg-[#0E1722] px-2 py-1.5" /></label>
        <button type="button" onClick={() => void train()} disabled={!manifestCount || (!!busy || training.active)} className="rounded bg-cyan-700 px-3 py-2 font-semibold hover:bg-cyan-600 disabled:opacity-40">OCR 후보 학습</button>
        <label className="min-w-[220px] flex-1">완료 후보 모델
          <select value={jobId} onChange={(event) => setJobId(event.target.value)} className="mt-1 block w-full rounded border border-slate-600 bg-[#0E1722] px-2 py-1.5">
            {!models.length && <option value="">완료 모델 없음</option>}
            {models.map((item) => <option key={item.job_id} value={item.job_id}>{item.job_id.slice(0, 12)} · epoch {item.metadata.best_epoch || '?'}</option>)}
          </select>
        </label>
        <button type="button" onClick={() => void evaluate()} disabled={!jobId || !datasetPath || (!!busy || training.active)} className="rounded border border-slate-600 px-3 py-2 hover:bg-slate-700 disabled:opacity-40">시험 분할 평가</button>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-[260px] flex-1">한 장 시험 이미지 경로
          <input value={imagePath} onChange={(event) => setImagePath(event.target.value)} placeholder="/path/to/text-crops/test.png"
            className="mt-1 block w-full rounded border border-slate-600 bg-[#0E1722] px-3 py-1.5 font-mono" />
        </label>
        <button type="button" onClick={() => void predict()} disabled={!jobId || !imagePath || (!!busy || training.active)} className="rounded border border-slate-600 px-3 py-2 hover:bg-slate-700 disabled:opacity-40">문자 읽기</button>
      </div>
      {busy && <p role="status" className="text-cyan-300"><Loader2 className="mr-1 inline h-3 w-3 animate-spin" />{busy === 'train' ? '학습 중' : '처리 중'}…</p>}
      {error && <p role="alert" className="rounded border border-rose-700 bg-rose-950/30 p-2 text-rose-200">{error}</p>}
      {notice && <p role="status" className="text-emerald-300">{notice}</p>}
      {evaluation && <div className="rounded border border-[#344255] bg-[#0E1722] p-3">
        시험 {evaluation.sample_count}장 · 정확히 일치 {(evaluation.exact_match_accuracy * 100).toFixed(1)}% · 문자 오류율 {(evaluation.character_error_rate * 100).toFixed(1)}%
        <div className="mt-2 max-h-32 overflow-y-auto font-mono text-slate-400">{evaluation.samples.map((item) => <div key={item.image} className="truncate">{item.image}: {item.reference_text} → {item.predicted_text}</div>)}</div>
      </div>}
      {prediction && <p className="rounded border border-cyan-700 bg-cyan-950/30 p-3">인식 후보: <strong className="text-cyan-200">{prediction.text || '(빈 문자열)'}</strong> · 후보 점수 {(prediction.confidence * 100).toFixed(1)}%</p>}
    </div>
  </details>;
};
