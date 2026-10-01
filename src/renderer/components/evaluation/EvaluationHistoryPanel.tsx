import { useEffect, useRef, useState } from 'react';
import { History, Loader2, RefreshCw } from 'lucide-react';
import { api, request,getApiPersistenceIdentity, type ProjectLabelSet } from '../../services/api';
import type { FlowModelTask } from '../../types';
import {useComputeStore} from '../../stores/useComputeStore';
import { useProjectStore } from '../../stores/useProjectStore';
import {EvaluationEvidencePanel} from './EvaluationEvidencePanel';
import {consumeReviewContext,evaluationOriginScope} from '../labeling/productDataWorkflow';
import type {EvidenceSample} from '../../services/evaluationEvidence';

interface EvaluationRecord {
  evaluation_id: string; created_at: number; evidence_sha256: string;
  binding: Record<string, unknown>;
  result: { job_id?: string; metrics?: Record<string, unknown>; test_predictions?: EvidenceSample[] };
  grouped_errors: Record<string, Record<string, { samples: number; errors: number; misses: number; overkill: number; unknown_truth?: number }>>;
}

export function EvaluationHistoryPanel({ sourceFolder, task, jobId }: { sourceFolder: string; task: FlowModelTask; jobId: string | null }) {
  const compute={...useComputeStore(),apiTransportIdentity:getApiPersistenceIdentity()};
  const [records, setRecords] = useState<EvaluationRecord[]>([]);
  const [selected, setSelected] = useState('');
  const [originImage,setOriginImage]=useState<{imageId?:string;filePath?:string}|null>(null);
  const [opened,setOpened]=useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [group, setGroup] = useState('product');
  const [historyTask, setHistoryTask] = useState<string>(task);
  const [labelsets,setLabelsets]=useState<ProjectLabelSet[]>([]);
  const [labelsetId,setLabelsetId]=useState('');
  const projectId = useProjectStore((s) => s.project?.id);
  const activeLabelset=useProjectStore(s=>s.project?.active_labelset_id||'default');
  const scope = JSON.stringify([evaluationOriginScope(projectId,sourceFolder,historyTask,labelsetId,compute),compute.transportRevision]);
  const currentScope = useRef(scope); currentScope.current = scope;
  useEffect(() => { setHistoryTask(task); }, [task, projectId]);
  useEffect(()=>{let active=true;setLabelsets([]);setLabelsetId('');void api.project.listLabelsets().then(r=>{if(active)setLabelsets(r.labelsets);}).catch(()=>{});return()=>{active=false;};},[projectId,revision,compute.transportRevision,compute.selectedProfileId,compute.apiTransportIdentity]);
  useEffect(() => {
    let current = true;
    setRecords([]); setSelected('');setOriginImage(null); setError(''); setBusy(false);
    if (!sourceFolder) return;
    setBusy(true);
    const params = new URLSearchParams({ source_dataset_path: sourceFolder, task: historyTask });
    if(labelsetId)params.set('labelset_id',labelsetId);
    void request<{ items: EvaluationRecord[] }>(`/api/evaluation/history?${params}`).then((response) => {
      if (current) { const origin=consumeReviewContext(localStorage,evaluationOriginScope(projectId,sourceFolder,historyTask,labelsetId||activeLabelset,compute),response.items.map(item=>item.evaluation_id));setRecords(response.items); setSelected(origin?.evaluation_id||response.items[0]?.evaluation_id||'');if(origin){setOriginImage({imageId:origin.image_id||undefined,filePath:origin.file_path});setOpened(true);} }
    }).catch((cause) => { if (current) setError(cause instanceof Error ? cause.message : '평가 이력을 불러오지 못했습니다.'); })
      .finally(() => { if (current) setBusy(false); });
    return () => { current = false; };
  }, [sourceFolder, historyTask, projectId, revision,labelsetId,activeLabelset,compute.transportRevision,compute.selectedProfileId,compute.apiTransportIdentity]);
  const reevaluate = async () => {
    const selectedRecord = records.find((item) => item.evaluation_id === selected);
    const targetJob = selectedRecord?.result.job_id || (historyTask === task ? jobId : null);
    if (!targetJob || !sourceFolder) return;
    const expected = scope;
    setBusy(true); setError('');
    try {
      await request('/api/evaluation/reevaluate', { method: 'POST', body: JSON.stringify({ source_dataset_path: sourceFolder, task: historyTask, job_id: targetJob, ...(selectedRecord?.binding.evaluation_dataset_path ? { dataset_path: selectedRecord.binding.evaluation_dataset_path } : {}) }) });
      if (currentScope.current === expected) setRevision((v) => v + 1);
    } catch (cause) { if (currentScope.current === expected) setError(cause instanceof Error ? cause.message : '재평가하지 못했습니다.'); }
    finally { if (currentScope.current === expected) setBusy(false); }
  };
  const record = records.find((item) => item.evaluation_id === selected);
  return <details open={opened} onToggle={event=>setOpened(event.currentTarget.open)} className="rounded border border-[#344255] bg-[#182332] text-xs text-slate-200">
    <summary className="flex cursor-pointer items-center gap-2 p-3 font-semibold"><History className="h-4 w-4 text-cyan-300" />평가 이력 · 제품/Lot별 오류</summary>
    <div className="space-y-3 border-t border-[#344255] p-3">
      <p className="text-slate-300">각 평가의 데이터·모델 출처와 결과를 별도 버전으로 보관합니다. 제품과 Lot은 데이터 화면에 입력한 값을 사용합니다.</p>
      <label>평가 모델 종류<select aria-label="평가 이력 모델 종류" value={historyTask} onChange={(e) => setHistoryTask(e.target.value)} className="ml-2 rounded border border-slate-600 bg-[#0E1722] p-2">{Object.entries({ classification: '분류', patch_classification: '패치 분류', detection: '객체 검출', segmentation: '영역 분할', anomaly: '이상 탐지', ocr: 'OCR', rotated_detection: '회전 객체 검출', rotation:'정방향 보정', enhancement: '이미지 개선', defect_gan: '결함 생성' }).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label className="ml-3">평가 당시 라벨 세트<select aria-label="평가 라벨 세트" value={labelsetId} onChange={e=>setLabelsetId(e.target.value)} className="ml-2 rounded border border-slate-600 bg-[#0E1722] p-2"><option value="">전체 세트</option>{labelsets.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <div className="flex flex-wrap gap-2"><button type="button" disabled={busy || !(record?.result.job_id || (historyTask === task && jobId))} onClick={() => void reevaluate()} className="rounded border border-cyan-700 px-3 py-2 text-cyan-200 disabled:opacity-40">선택 모델 재평가 · 새 이력 저장</button><button type="button" disabled={busy} aria-label="평가 이력 새로고침" onClick={() => setRevision((v) => v + 1)} className="rounded border border-slate-600 p-2"><RefreshCw className="h-4 w-4" /></button></div>
      {busy && <p role="status"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />평가 기록 확인 중…</p>}
      {error && <p role="alert" className="rounded border border-rose-700 p-2 text-rose-200">{error}</p>}
      {!busy && !records.length && <p className="text-slate-300">저장된 평가 이력이 없습니다.</p>}
      {!!records.length && <label className="block">모델별 저장 평가<select value={selected} onChange={(e) => {setSelected(e.target.value);setOriginImage(null);}} className="mt-1 block w-full rounded border border-slate-600 bg-[#0E1722] px-2 py-2">{Array.from(new Set(records.map(r=>r.result.job_id||'unknown'))).map(model=><optgroup key={model} label={`모델 ${model}`}>{records.filter(r=>r.result.job_id===model).map(item=><option key={item.evaluation_id} value={item.evaluation_id}>{new Date(item.created_at*1000).toLocaleString()} · {String(item.binding.labelset_id||'default')} · {item.evaluation_id.slice(-8)}</option>)}</optgroup>)}</select></label>}
      {record && <>
        {!!Object.keys((record.binding.threshold_settings||{}) as Record<string,unknown>).length&&<p className="rounded bg-[#0E1722] p-2 text-cyan-200">평가 임계값: {JSON.stringify(record.binding.threshold_settings)}</p>}
        <label>오류 집계<select aria-label="평가 오류 집계 기준" value={group} onChange={(e) => setGroup(e.target.value)} className="ml-2 rounded border border-slate-600 bg-[#0E1722] p-2"><option value="product">제품</option><option value="lot">Lot</option><option value="ground_truth">정답 클래스</option></select></label>
        <table className="w-full border-collapse text-left"><thead><tr className="border-b border-slate-600 text-slate-300"><th className="p-2">그룹</th><th>이미지</th><th>오류</th><th>미검</th><th>과검</th><th>정답 미확인</th></tr></thead><tbody>{Object.entries(record.grouped_errors[group] || {}).map(([key, counts]) => <tr key={key} className="border-b border-[#344255]"><td className="p-2">{key === '(unassigned)' ? '미지정' : key}</td><td>{counts.samples}</td><td>{counts.errors}</td><td>{counts.misses}</td><td>{counts.overkill}</td><td>{counts.unknown_truth || 0}</td></tr>)}</tbody></table>
        {!Object.keys(record.grouped_errors[group] || {}).length && <p className="text-slate-400">이 모델은 영역·문자·복원 지표로 평가합니다. 이진 정상/불량 오류를 임의로 집계하지 않습니다.</p>}
        <details><summary className="cursor-pointer text-cyan-200">저장된 평가 지표·이미지 결과</summary><pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded bg-[#0E1722] p-3">{JSON.stringify(record.result, null, 2)}</pre></details>
        <EvaluationEvidencePanel samples={record.result.test_predictions||[]} initialPath={originImage?.filePath} initialImageId={originImage?.imageId}/>
        <details><summary className="cursor-pointer text-cyan-200">평가 버전·데이터·모델 해시 확인</summary><pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-all rounded bg-[#0E1722] p-3">{JSON.stringify({ evaluation_id: record.evaluation_id, evidence_sha256: record.evidence_sha256, binding: record.binding }, null, 2)}</pre></details>
      </>}
    </div>
  </details>;
}
