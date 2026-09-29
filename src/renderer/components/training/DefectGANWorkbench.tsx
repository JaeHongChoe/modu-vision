import React, { useEffect, useState } from 'react';
import { Images, Loader2, RefreshCw } from 'lucide-react';
import { api, type DefectGANCandidate, type DefectGANCropRow, type DefectGANModelSummary } from '../../services/api';
import { useProjectStore } from '../../stores/useProjectStore';

function parseCrops(value: string): DefectGANCropRow[] {
  const lines = value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (!lines.length) throw new Error('결함 이미지와 자를 영역을 입력하세요.');
  return lines.map((line, index) => {
    const [image, bboxText, split, ...extra] = line.split('\t');
    const coordinates = bboxText?.split(',').map((part) => Number(part.trim())) ?? [];
    if (!image?.trim() || extra.length || !['train', 'val', 'test'].includes(split) ||
        coordinates.length !== 4 || coordinates.some((coordinate) => !Number.isInteger(coordinate))) {
      throw new Error(`${index + 1}행은 이미지 상대 경로, x1,y1,x2,y2, train/val/test를 탭으로 나누세요.`);
    }
    const [x1, y1, x2, y2] = coordinates;
    if (x1 < 0 || y1 < 0 || x2 - x1 < 16 || y2 - y1 < 16) {
      throw new Error(`${index + 1}행의 결함 영역은 각 변이 최소 16px이어야 합니다.`);
    }
    return { image: image.trim(), bbox: [x1, y1, x2, y2], split: split as DefectGANCropRow['split'] };
  });
}

function errorText(cause: unknown): string {
  if (cause instanceof Error) return cause.message;
  if (cause && typeof cause === 'object' && 'message' in cause) return String(cause.message);
  return String(cause);
}

export const DefectGANWorkbench: React.FC = () => {
  const projectDir = useProjectStore((state) => state.projectDir);
  const [datasetPath, setDatasetPath] = useState('');
  const [rowsText, setRowsText] = useState('');
  const [sampleCount, setSampleCount] = useState<number | null>(null);
  const [epochs, setEpochs] = useState(20);
  const [count, setCount] = useState(4);
  const [seed, setSeed] = useState(0);
  const [models, setModels] = useState<DefectGANModelSummary[]>([]);
  const [jobId, setJobId] = useState('');
  const [candidates, setCandidates] = useState<DefectGANCandidate[]>([]);
  const [reviewDir, setReviewDir] = useState('');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<'manifest' | 'train' | 'generate' | null>(null);

  useEffect(() => {
    setDatasetPath(''); setRowsText(''); setSampleCount(null);
    setModels([]); setJobId(''); setCandidates([]); setReviewDir(''); setNotice(''); setError('');
    if (!projectDir) return;
    let active = true;
    void api.defectGAN.models().then(({ models: items }) => {
      if (!active || useProjectStore.getState().projectDir !== projectDir) return;
      setModels(items); setJobId(items[0]?.job_id || '');
    }).catch((cause) => { if (active) setError(errorText(cause)); });
    return () => { active = false; };
  }, [projectDir]);

  const sameProject = () => useProjectStore.getState().projectDir === projectDir;
  const loadManifest = async () => {
    if (!datasetPath.trim() || !projectDir || busy) return;
    setBusy('manifest'); setError(''); setNotice('');
    try {
      const result = await api.defectGAN.manifest(datasetPath.trim());
      if (!sameProject()) return;
      setRowsText(result.samples.map((row) => `${row.image}\t${row.bbox.join(',')}\t${row.split}`).join('\n'));
      setSampleCount(result.sample_count);
      setNotice(`저장된 결함 영역 ${result.sample_count}개와 원본 이미지 해시를 확인했습니다.`);
    } catch (cause) { if (sameProject()) setError(errorText(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };
  const saveManifest = async () => {
    if (!datasetPath.trim() || !projectDir || busy) return;
    let samples: DefectGANCropRow[];
    try { samples = parseCrops(rowsText); } catch (cause) { setError(errorText(cause)); return; }
    setBusy('manifest'); setError(''); setNotice('');
    try {
      const result = await api.defectGAN.saveManifest(datasetPath.trim(), samples);
      if (!sameProject()) return;
      setSampleCount(result.sample_count);
      setNotice(`결함 영역 ${result.sample_count}개를 이미지 해시와 함께 저장했습니다.`);
    } catch (cause) { if (sameProject()) setError(errorText(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };
  const train = async () => {
    if (!datasetPath.trim() || !projectDir || !sampleCount || busy) return;
    setBusy('train'); setError(''); setNotice(''); setCandidates([]);
    try {
      const result = await api.defectGAN.train(datasetPath.trim(), epochs);
      const refreshed = await api.defectGAN.models();
      if (!sameProject()) return;
      setModels(refreshed.models); setJobId(result.job_id);
      setNotice(`생성 후보 모델 학습 완료 · ${result.job_id.slice(0, 8)}. 생성한 이미지는 별도로 검토해야 합니다.`);
    } catch (cause) { if (sameProject()) setError(errorText(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };
  const generate = async () => {
    if (!projectDir || !jobId || busy) return;
    setBusy('generate'); setError(''); setNotice(''); setCandidates([]); setReviewDir('');
    try {
      const result = await api.defectGAN.generate(jobId, count, seed);
      if (!sameProject()) return;
      setCandidates(result.candidates); setReviewDir(result.review_dir);
      setNotice(`검토 대기 이미지 ${result.candidates.length}장을 만들었습니다. 학습 데이터에 자동으로 추가되지 않습니다.`);
    } catch (cause) { if (sameProject()) setError(errorText(cause)); }
    finally { if (sameProject()) setBusy(null); }
  };

  return <details className="rounded-xl border border-[#344255] bg-[#141D2B] text-xs text-slate-200">
    <summary className="flex cursor-pointer items-center gap-2 px-4 py-3 font-semibold text-slate-100">
      <Images className="h-4 w-4 text-violet-400" /> 결함 이미지 생성 실험 <span className="font-normal text-slate-400">학습된 GAN · 검토 대기 후보</span>
    </summary>
    <div className="space-y-4 border-t border-[#344255] p-4">
      <p className="leading-5 text-slate-400">실제 결함이 보이는 영역을 지정해 학습합니다. 생성 이미지는 원본 라벨이나 학습 분할에 자동으로 섞이지 않습니다.</p>
      <label className="block text-slate-300">원본 이미지 폴더 경로
        <input value={datasetPath} onChange={(event) => { setDatasetPath(event.target.value); setSampleCount(null); }}
          placeholder="/path/to/defect-images" className="mt-1 w-full rounded border border-slate-600 bg-[#0E1722] px-3 py-2 font-mono text-slate-100" />
      </label>
      <label className="block text-slate-300">결함 영역 표 · 한 줄에 이미지 상대 경로 ↹ x1,y1,x2,y2 ↹ train/val/test
        <textarea value={rowsText} onChange={(event) => { setRowsText(event.target.value); setSampleCount(null); }} rows={4}
          placeholder={'images/defect_001.png\t10,20,74,84\ttrain\nimages/defect_002.png\t5,8,69,72\ttrain'}
          className="mt-1 w-full rounded border border-slate-600 bg-[#0E1722] px-3 py-2 font-mono text-slate-100" />
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => void loadManifest()} disabled={!datasetPath || !!busy} className="rounded border border-slate-600 px-3 py-1.5 hover:bg-slate-700 disabled:opacity-40"><RefreshCw className="mr-1 inline h-3 w-3" />저장된 영역 읽기</button>
        <button type="button" onClick={() => void saveManifest()} disabled={!datasetPath || !rowsText.trim() || !!busy} className="rounded border border-violet-700 bg-violet-950/40 px-3 py-1.5 text-violet-200 hover:bg-violet-900/40 disabled:opacity-40">영역과 원본 해시 저장</button>
        {sampleCount !== null && <span className="text-emerald-300">검증된 영역 {sampleCount}개</span>}
      </div>
      <div className="flex flex-wrap items-end gap-2 border-t border-[#344255] pt-4">
        <label>학습 epoch<input type="number" min="1" max="500" value={epochs} onChange={(event) => setEpochs(Math.max(1, Math.min(500, Number(event.target.value) || 1)))}
          className="mt-1 block w-20 rounded border border-slate-600 bg-[#0E1722] px-2 py-1.5" /></label>
        <button type="button" onClick={() => void train()} disabled={!sampleCount || !!busy} className="rounded bg-violet-700 px-3 py-2 font-semibold hover:bg-violet-600 disabled:opacity-40">생성 모델 학습</button>
        <label className="min-w-[220px] flex-1">완료 후보 모델
          <select value={jobId} onChange={(event) => { setJobId(event.target.value); setCandidates([]); }} className="mt-1 block w-full rounded border border-slate-600 bg-[#0E1722] px-2 py-1.5">
            {!models.length && <option value="">완료 모델 없음</option>}
            {models.map((model) => <option key={model.job_id} value={model.job_id}>{model.job_id.slice(0, 12)} · epoch {model.epochs} · 미검증</option>)}
          </select>
        </label>
        <label>생성 장수<input type="number" min="1" max="20" value={count} onChange={(event) => setCount(Math.max(1, Math.min(20, Number(event.target.value) || 1)))}
          className="mt-1 block w-16 rounded border border-slate-600 bg-[#0E1722] px-2 py-1.5" /></label>
        <label>생성 시드<input type="number" min="0" value={seed} onChange={(event) => setSeed(Math.max(0, Number(event.target.value) || 0))}
          className="mt-1 block w-20 rounded border border-slate-600 bg-[#0E1722] px-2 py-1.5" /></label>
        <button type="button" onClick={() => void generate()} disabled={!jobId || !!busy} className="rounded border border-violet-600 px-3 py-2 text-violet-200 hover:bg-violet-950 disabled:opacity-40">후보 생성</button>
      </div>
      {busy && <p role="status" className="text-violet-300"><Loader2 className="mr-1 inline h-3 w-3 animate-spin" />{busy === 'train' ? 'GAN 학습 중' : '처리 중'}…</p>}
      {error && <p role="alert" className="rounded border border-rose-700 bg-rose-950/30 p-2 text-rose-200">{error}</p>}
      {notice && <p role="status" className="text-emerald-300">{notice}</p>}
      {!!candidates.length && <div className="space-y-2 rounded border border-[#344255] bg-[#0E1722] p-3">
        <div className="font-semibold text-slate-200">생성 후보 · 검토 대기</div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 md:grid-cols-6">
          {candidates.map((candidate) => <div key={candidate.id} className="rounded border border-slate-700 bg-[#141D2B] p-1.5">
            <img src={candidate.preview_data_url} alt={`결함 생성 후보 ${candidate.id}`} className="aspect-square w-full rounded object-contain" />
            <div className="mt-1 truncate font-mono text-[10px] text-slate-400" title={candidate.path}>{candidate.id}</div>
          </div>)}
        </div>
        <p className="break-all font-mono text-[10px] text-slate-400">검토 폴더: {reviewDir}</p>
      </div>}
    </div>
  </details>;
};
