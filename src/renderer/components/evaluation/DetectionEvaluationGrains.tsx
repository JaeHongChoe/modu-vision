import * as React from 'react';

export interface DetectionGrainSummary {
  matchedClasses: number;
  classSamples: number;
  threshold: number;
  imageTp: number;
  imageFn: number;
  imageFp: number;
  imageTn: number;
  map50: number | null;
  metricContract?: string;
  apAvailable?: boolean;
}

export function summarizeDetectionGrains(input: {
  matrix: number[][];
  verdicts: string[];
  threshold: number;
  map50: unknown;
  metricContract?: unknown;
  apAvailable?: unknown;
}): DetectionGrainSummary {
  const classSamples = input.matrix.reduce((sum, row) => sum + row.reduce((total, value) => total + value, 0), 0);
  const matchedClasses = input.matrix.reduce((sum, row, index) => sum + (row[index] || 0), 0);
  return {
    matchedClasses,
    classSamples,
    threshold: input.threshold,
    imageTp: input.verdicts.filter((verdict) => verdict === 'CORRECT_NG').length,
    imageFn: input.verdicts.filter((verdict) => verdict === 'ESCAPE').length,
    imageFp: input.verdicts.filter((verdict) => verdict === 'OVERKILL').length,
    imageTn: input.verdicts.filter((verdict) => verdict === 'CORRECT_OK').length,
    map50: typeof input.map50 === 'number' && Number.isFinite(input.map50) ? input.map50 : null,
    ...(typeof input.metricContract === 'string' ? {metricContract: input.metricContract} : {}),
    ...(typeof input.apAvailable === 'boolean' ? {apAvailable: input.apAvailable} : {}),
  };
}

export const DetectionEvaluationGrains: React.FC<{
  summary: DetectionGrainSummary;
  language: string;
  simple?: boolean;
}> = ({ summary, language, simple = false }) => {
  const ko = language === 'ko';
  const technicalMetrics = (
    <>
      <div>
        <strong className="text-cyan-200">{ko ? '이미지 OK/NG' : 'Image OK/NG'} (τ={summary.threshold.toFixed(2)})</strong>
        <span className="ml-2 font-mono">TP {summary.imageTp} · FN {summary.imageFn} · FP {summary.imageFp} · TN {summary.imageTn}</span>
        <p className="text-slate-400">{ko ? '이미지별 최대 결함 점수를 현재 임계값과 비교합니다.' : 'Compares each image’s maximum defect score with the current threshold.'}</p>
      </div>
      <div>
        <strong className="text-slate-200">{ko ? '상위 박스 클래스' : 'Top box class'}</strong>
        <span className="ml-2 font-mono">{summary.matchedClasses}/{summary.classSamples} {ko ? '일치' : 'match'}</span>
        <p className="text-slate-400">{ko ? '아래 표의 행·열 일치율은 τ·IoU 미적용: 이미지에서 가장 높은 점수의 박스 클래스만 비교합니다.' : 'The row and column match rates below ignore τ and IoU; they compare only the highest-scoring box class per image.'}</p>
      </div>
      <div>
        <strong className="text-amber-200">{ko ? '객체 위치 정합' : 'Object localization'}</strong>
        <span className="ml-2 font-mono">mAP@IoU 0.5: {summary.map50 === null ? '—' : summary.map50.toFixed(4)}</span>
        <p className="text-slate-400">{ko ? '예측 박스와 정답 박스의 위치 일치를 평가합니다. 위의 클래스 일치율·이미지 판정과 별도입니다.' : 'Measures predicted box overlap with ground truth, separately from top-class match and image verdicts.'}</p>
        <p className="text-slate-400">{summary.metricContract === 'detection-ap-v2'
          ? ko ? '검출 AP 기준 v2 · 정답 객체가 있는 클래스만 평균하며 이미지·클래스당 최대 100개 예측을 사용합니다. crowd·ignore·크기별 평가와는 별도입니다.'
            : 'Detection AP v2 · averages classes with ground truth, up to 100 predictions per image/class. Crowd, ignored and area-specific evaluation are separate.'
          : ko ? '이전 계산 기준의 저장 결과입니다. 새 기준과 점수를 바로 비교하지 마세요.'
            : 'Saved result under the previous metric definition; compare versions explicitly.'}</p>
      </div>
    </>
  );
  return (
    <div role="note" className="space-y-1.5 rounded border border-[#364357] bg-[#101722] p-2.5 text-[11px] text-slate-300">
      {summary.apAvailable === false && <p>{ko ? '정답 객체가 없어 위치 품질을 계산하지 않았습니다. 이미지 판정 결과는 별도로 확인하세요.' : 'No ground-truth objects: localization quality is unavailable. Inspect image verdicts separately.'}</p>}
      {simple ? <>
        <p className="text-[13px]">{ko
          ? `맞힌 이미지 ${summary.imageTp + summary.imageTn}장 · 놓친 불량 ${summary.imageFn}장 · 잘못 잡은 정상 ${summary.imageFp}장`
          : `Correct images: ${summary.imageTp + summary.imageTn} images · Missed defects: ${summary.imageFn} images · Normal images flagged: ${summary.imageFp} images`}</p>
        <details>
          <summary className="cursor-pointer text-slate-400">{ko ? '자세히 · 검출 품질 지표' : 'Details · detection quality metrics'}</summary>
          <div className="mt-2 space-y-1.5">{technicalMetrics}</div>
        </details>
      </> : technicalMetrics}
    </div>
  );
};
