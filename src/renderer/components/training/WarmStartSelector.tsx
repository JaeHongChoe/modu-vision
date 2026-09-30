import { useEffect, useState } from 'react';
import { request } from '../../services/api';
import { useProjectStore } from '../../stores/useProjectStore';

type Parent = {job_id: string; checkpoint_sha256: string; semantics: string};

export function WarmStartSelector({family, datasetPath, value, onChange, disabled, refreshKey}: {
  family: 'ocr' | 'defect-gan' | 'rotated-detection' | 'enhancement'; datasetPath: string;
  value: string; onChange: (value: string) => void; disabled: boolean;
  refreshKey?: string | null;
}) {
  const projectDir = useProjectStore(state => state.projectDir);
  const source = useProjectStore(state => state.project?.source_dataset_dir);
  const labelset = useProjectStore(state => state.project?.active_labelset_id);
  const [parents, setParents] = useState<Parent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let current = true;
    onChange(''); setParents([]); setError(''); setLoading(false);
    if (!projectDir || !datasetPath.trim()) return () => { current = false; };
    setLoading(true);
    const query = new URLSearchParams({dataset_path: datasetPath.trim()});
    request<{parents: Parent[]}>(`/api/${family}/warm-start-parents?${query}`).then(result => {
      if (current) setParents(result.parents);
    }).catch(cause => { if (current) setError(String(cause.message || cause)); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [family, datasetPath, projectDir, source, labelset, onChange, refreshKey]);
  return <div className="rounded border border-[#344255] bg-[#0D1725] p-3 text-xs">
    <label className="block text-slate-300">재학습 시작 모델
      <select aria-label={`${family} 재학습 시작 모델`} value={value} disabled={disabled || loading} onChange={event => onChange(event.target.value)} className="mt-1 w-full rounded border border-slate-600 bg-[#0B1520] px-2 py-2">
        <option value="">{loading ? '호환 모델 확인 중…' : '새 모델로 학습'}</option>
        {parents.map(parent => <option key={parent.job_id} value={parent.job_id}>{parent.job_id.slice(0, 12)} · SHA {parent.checkpoint_sha256.slice(0, 12)}</option>)}
      </select>
    </label>
    <p className="mt-2 text-slate-400">현재 출처·모델 구조와 호환되는 완료 모델만 선택합니다. 부모 가중치를 보존하고 새 후보로 저장합니다. 이 전문 모델의 학습 위치는 로컬입니다.</p>
    {error && <p role="alert" className="mt-2 text-amber-300">호환 모델 확인: {error}</p>}
  </div>;
}
