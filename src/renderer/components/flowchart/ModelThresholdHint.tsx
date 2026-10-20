/**
 * Under a model node's threshold: the decision threshold this model uses outside step 4 (the value single-model export
 * applies) and where it comes from. "이 값 쓰기" sets the node to it as an ordinary edit, so the flow is saved and
 * evaluated again as usual; nothing changes a saved or approved flow by itself (audit F-08 / A4).
 */
import React, { useEffect, useState } from 'react';
import { request } from '../../services/api';

export interface ModelDecisionThreshold {
  job_id: string; threshold: number; source: 'evaluation_calibration' | 'saved_model_threshold' | string;
  calibrated: boolean; not_transferable_reason: string | null; model_threshold: number;
}

const reasons: Record<string, string> = {
  checkpoint_binding_mismatch: '모델 파일이 평가 때와 달라',
  calibration_evidence_mismatch: '평가 기록이 바뀌어',
  calibration_requires_both_classes: '정상·불량 시험 이미지가 모두 있어야 해서',
};

export function thresholdHintText(value: ModelDecisionThreshold, current: number): { text: string; offer: boolean } {
  const same = Math.abs(value.threshold - current) < 1e-6;
  if (value.calibrated) {
    return { offer: !same, text: `평가(4단계)에서 정한 기준 점수 ${value.threshold.toFixed(2)} · 단일 모델 내보내기도 이 값을 씁니다${same ? ' (이 노드와 같음)' : ''}` };
  }
  const why = value.not_transferable_reason ? `${reasons[value.not_transferable_reason] || '평가 보정을 이 모델에 쓸 수 없어'} ` : '';
  return { offer: !same, text: `${why}모델 기본 기준 점수 ${value.threshold.toFixed(2)}를 씁니다${same ? ' (이 노드와 같음)' : ''}` };
}

export const ModelThresholdHint: React.FC<{ jobId: string; current: number; onUse: (value: number) => void; disabled?: boolean }> = ({ jobId, current, onUse, disabled }) => {
  const [value, setValue] = useState<ModelDecisionThreshold | null>(null);
  useEffect(() => {
    let live = true;
    setValue(null);
    if (!jobId) return undefined;
    void request<ModelDecisionThreshold>(`/api/models/decision-threshold?job_id=${encodeURIComponent(jobId)}`)
      .then(result => { if (live) setValue(result); }).catch(() => { if (live) setValue(null); });
    return () => { live = false; };
  }, [jobId]);
  if (!value) return null;
  const hint = thresholdHintText(value, current);
  return (
    <p aria-label="모델 기준 점수" className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
      <span>{hint.text}</span>
      {hint.offer && <button type="button" disabled={disabled} onClick={() => onUse(value.threshold)}
        className="rounded border border-cyan-700 px-2 py-0.5 text-cyan-100 disabled:opacity-40">이 값 쓰기</button>}
    </p>
  );
};
