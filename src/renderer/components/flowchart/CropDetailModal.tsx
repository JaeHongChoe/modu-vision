/**
 * src/renderer/components/flowchart/CropDetailModal.tsx
 * Single ROI Detailed Inspection Modal.
 * Solid dark steel chassis (#1A212E, border #2B3547), zero backdrop blur,
 * strict tabular-nums font-mono, and operator paging controls.
 */

import React, { useEffect, useMemo } from 'react';
import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Maximize2,
  X,
  XCircle,
} from 'lucide-react';
import { useFlowchartStore } from '../../stores/useFlowchartStore';
import type { FlowchartCrop, FlowchartPipeline, FlowNode } from '../../types';

interface CropDetailModalProps {
  crop: FlowchartCrop | null;
  onClose: () => void;
}

const modelNodeForCrop = (pipeline: FlowchartPipeline | null, crop: FlowchartCrop): FlowNode | undefined => {
  if (!pipeline) return undefined;
  const modelNodes = pipeline.nodes.filter((node) =>
    node.data.node_type === 'inspection' || node.data.node_type === 'detection_crop');
  const sourceNode = pipeline.nodes.find((node) => node.id === crop.source_node_id)
    || pipeline.nodes.find((node) => crop.roi_id.startsWith(`${node.id}:`));
  if (sourceNode?.data.node_type === 'blob_measure') {
    const parent = pipeline.edges.find((edge) => edge.target === sourceNode.id);
    const segmentation = modelNodes.find((node) => node.id === parent?.source && node.data.task === 'segmentation');
    if (segmentation) return segmentation;
  }
  const taggedNode = modelNodes.find((node) => node.id === sourceNode?.id || crop.roi_id.startsWith(`${node.id}:`));
  if (taggedNode) return taggedNode;

  // The engine adds a node ID to ROI IDs only when multiple model leaves feed the decision.
  const decisionIds = new Set(pipeline.nodes
    .filter((node) => node.data.node_type === 'decision')
    .map((node) => node.id));
  const producerNodes = modelNodes.filter((node) => pipeline.edges.some((edge) =>
    edge.source === node.id && decisionIds.has(edge.target)));
  return producerNodes.length === 1 ? producerNodes[0] : undefined;
};

export const CropDetailModal: React.FC<CropDetailModalProps> = ({ crop, onClose }) => {
  const { pipeline, executionResult, setInspectedCrop } = useFlowchartStore();

  const crops = useMemo(() => {
    return executionResult?.crops ?? [];
  }, [executionResult]);

  // Current crop index for prev/next paging
  const currentIndex = useMemo(() => {
    if (!crop) return -1;
    return crops.findIndex((c) => c.roi_id === crop.roi_id);
  }, [crop, crops]);

  // Keyboard navigation listener (←, →, Esc)
  useEffect(() => {
    if (!crop) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      } else if (e.key === 'ArrowLeft' && currentIndex > 0) {
        setInspectedCrop(crops[currentIndex - 1]);
      } else if (e.key === 'ArrowRight' && currentIndex < crops.length - 1) {
        setInspectedCrop(crops[currentIndex + 1]);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [crop, currentIndex, crops, onClose, setInspectedCrop]);

  if (!crop) return null;

  const [x1, y1, x2, y2] = crop.bbox;
  const width = Math.round(x2 - x1);
  const height = Math.round(y2 - y1);
  const areaPx = width * height;
  const isNg = crop.verdict === 'NG';

  // Read the threshold from the model that produced this ROI, including detector-only flows.
  const inspectNode = modelNodeForCrop(pipeline, crop);
  const threshold = crop.score_spec?.threshold ?? inspectNode?.data.threshold;
  const distance = crop.score_spec?.domain === 'distance';
  const scoreUnit = crop.score_spec?.unit || 'probability';
  const hasDefectArea = typeof crop.defect_area_px === 'number' && Number.isFinite(crop.defect_area_px);
  const hasBlobCount = typeof crop.blob_count === 'number' && Number.isFinite(crop.blob_count);
  const hasLargestBlobArea = typeof crop.largest_blob_area_px === 'number' && Number.isFinite(crop.largest_blob_area_px);
  const confidence = typeof crop.confidence === 'number' && Number.isFinite(crop.confidence) ? crop.confidence : null;
  const isSegmentation = inspectNode?.data.task === 'segmentation' || hasBlobCount || hasLargestBlobArea;
  const isFullImageSegmentation = isSegmentation && !pipeline?.nodes.some((n) => n.data.node_type === 'detection_crop');
  const minimumDefectArea = Number(inspectNode?.data.params?.min_defect_area_px ?? 8);
  const scale = distance ? Math.max(crop.defect_score, threshold || 0, 1e-9) * 1.1 : 1;
  const scorePercent = crop.defect_score / scale * 100;
  const thresholdPercent = typeof threshold === 'number' && Number.isFinite(threshold)
    ? threshold / scale * 100 : null;
  const deltaPercent = thresholdPercent === null ? null : scorePercent - thresholdPercent;

  return (
    <div className="fixed inset-0 z-50 bg-[#000000]/80 flex items-center justify-center p-6 select-none animate-in fade-in duration-100">
      <div className="bg-[#1A212E] border border-[#2B3547] rounded w-full max-w-3xl flex flex-col shadow-2xl overflow-hidden">
        {/* =================================================================== */}
        {/* Modal Header */}
        {/* =================================================================== */}
        <div className="h-12 px-5 bg-[#131822] border-b border-[#2B3547] flex items-center justify-between">
          <div className="flex items-center space-x-2.5">
            <Maximize2 className="w-4 h-4 text-[#3B82F6]" />
            <h3 className="font-bold text-xs font-mono uppercase tracking-wider text-slate-100">
              검사 영역 상세 결과 — {crop.label} ({crop.roi_id})
            </h3>
          </div>
          <button
            onClick={onClose}
            className="p-1 hover:bg-[#222B3D] border border-transparent hover:border-[#2B3547] rounded text-slate-300 hover:text-white transition-colors cursor-pointer"
            title="닫기 (Esc)"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* =================================================================== */}
        {/* Modal Body: 2-Column Inspection View */}
        {/* =================================================================== */}
        <div className="p-5 flex space-x-5 bg-[#1A212E]">
          {/* Left Column: High-Res Crop Viewport */}
          <div className="w-1/2 flex flex-col items-center justify-center bg-[#0B0E14] rounded border border-[#2B3547] p-4">
            <div className="w-full flex-1 flex items-center justify-center min-h-[240px] max-h-72 overflow-hidden rounded bg-[#000000] border border-[#2B3547] relative">
              <img
                src={crop.crop_thumbnail}
                alt={crop.label}
                className="max-h-64 max-w-full object-contain"
              />
              <div className="absolute top-2 left-2 bg-[#0B0E14]/80 border border-[#2B3547] px-1.5 py-0.5 rounded text-[9px] font-mono text-slate-300">
                ROI ID: {crop.roi_id}
              </div>
            </div>

            {crop.anomaly_map && <figure className="mt-3 w-full rounded border border-slate-700 bg-[#101A28] p-2">
              <img src={crop.anomaly_map} alt="이상 점수 분포" className="max-h-40 w-full object-contain" />
              <figcaption className="mt-2 text-center text-[11px] text-slate-300">
                {crop.map_semantics === 'patch_score' ? '패치별 이상 점수 · 결함 위치를 검토하는 참고 자료' : '이상 점수 분포'}
              </figcaption>
              {crop.map_semantics === 'patch_score' && <p className="mt-1 text-center text-[10px] text-amber-200">결함의 정확한 면적과 개수는 영역 분할 모델로 확인하세요.</p>}
            </figure>}

            <div className="w-full mt-3 pt-2.5 border-t border-[#2B3547] flex items-center justify-between text-[11px] font-mono text-slate-300">
              <span>검사 이미지 영역: <strong className="text-white tabular-nums">{width} × {height} px</strong></span>
              <span>면적: <strong className="text-white tabular-nums">{areaPx.toLocaleString()} px²</strong></span>
            </div>
          </div>

          {/* Right Column: Technical Measurement & Diagnostics */}
          <div className="w-1/2 max-h-[75vh] overflow-y-auto flex flex-col justify-between text-xs space-y-3.5">
            <div>
              {/* Verdict Annunciator Banner */}
              <div
                className={`p-3 rounded border flex items-center justify-between ${
                  isNg
                    ? 'bg-[#2D1216] border-[#EF4444] text-[#EF4444]'
                    : 'bg-[#0E2A20] border-[#10B981] text-[#10B981]'
                }`}
              >
                <div className="flex items-center space-x-2">
                  {isNg ? <XCircle className="w-4 h-4" /> : <CheckCircle2 className="w-4 h-4" />}
                  <span className="font-mono font-bold text-xs uppercase">
                    {hasBlobCount || hasLargestBlobArea ? 'Blob 측정 판정' : '로컬 모델 판정'}: {crop.verdict}
                  </span>
                </div>
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-[#0B0E14]/60 border border-current">
                  {isNg ? '결함 기준 초과' : '설정 기준 이내'}
                </span>
              </div>

              {/* Defect Score Precision Gauge */}
              <div className="mt-3 bg-[#0B0E14] p-3 rounded border border-[#2B3547]">
                <div className="flex justify-between text-[11px] font-mono mb-1.5">
                  <span className="text-slate-400">{isSegmentation ? '최고 결함 픽셀 확률:' : '결함 / 이상 점수:'}</span>
                  <div className="flex items-center space-x-2">
                    <span className={`font-bold tabular-nums ${isNg ? 'text-[#EF4444]' : 'text-[#10B981]'}`}>
                      {distance ? `${crop.defect_score.toFixed(4)} ${scoreUnit}` : `${scorePercent.toFixed(1)}%`}
                    </span>
                    <span className="text-slate-400 text-[10px] tabular-nums">
                      ({crop.defect_score.toFixed(4)})
                    </span>
                  </div>
                </div>

                {/* Progress Bar with Threshold Pin */}
                <div className="w-full bg-[#1A212E] h-2 rounded border border-[#2B3547] overflow-hidden relative">
                  <div
                    className={`h-full transition-all ${isNg ? 'bg-[#EF4444]' : 'bg-[#10B981]'}`}
                    style={{ width: `${Math.min(100, Math.max(0, scorePercent))}%` }}
                  />
                  {/* Threshold Pin Marker */}
                  {thresholdPercent !== null && (
                    <div
                      className="absolute top-0 bottom-0 w-0.5 bg-[#F59E0B] z-10"
                      style={{ left: `${thresholdPercent}%` }}
                      title={`기준 임계값: ${threshold} ${scoreUnit}`}
                    />
                  )}
                </div>

                {/* Gauge Labels */}
                <div className="flex justify-between text-[9px] text-slate-400 mt-1.5 font-mono">
                  <span>{distance?'0':'0.0%'}</span>
                  <span className="text-[#F59E0B]">
                    임계 기준: {thresholdPercent === null ? '확인 불가' : distance ? `${threshold} ${scoreUnit}` : `${thresholdPercent.toFixed(1)}%`}
                  </span>
                  <span>{distance?scale.toFixed(2):'100.0%'}</span>
                </div>

                {/* Delta Calculation */}
                <div className="mt-2 pt-2 border-t border-[#2B3547] flex justify-between text-[10px] font-mono">
                  <span className="text-slate-400">임계값 초과 편차 (Δ):</span>
                  <span className={`font-bold tabular-nums ${deltaPercent !== null && deltaPercent > 0 ? 'text-[#EF4444]' : 'text-[#10B981]'}`}>
                    {deltaPercent === null ? '—' : distance ? `${(crop.defect_score-(threshold||0)).toFixed(4)} ${scoreUnit}` : deltaPercent > 0 ? `+${deltaPercent.toFixed(1)}%` : `${deltaPercent.toFixed(1)}%`}
                  </span>
                </div>
              </div>

              {/* Geometric Coordinate Breakdown Table */}
              <div className="mt-3 bg-[#0B0E14] p-3 rounded border border-[#2B3547] text-[11px] font-mono space-y-1.5">
                <div className="flex justify-between border-b border-[#2B3547]/60 pb-1">
                  <span className="text-slate-400">{isSegmentation ? '분할 모델 결과:' : '검출 정보:'}</span>
                  <span className="text-white font-bold">{crop.flaw_type}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">좌상단 좌표 (X₁, Y₁):</span>
                  <span className="text-slate-200 tabular-nums">({x1} px, {y1} px)</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">우하단 좌표 (X₂, Y₂):</span>
                  <span className="text-slate-200 tabular-nums">({x2} px, {y2} px)</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">바운딩 박스 (BBox):</span>
                  <span className="text-[#3B82F6] font-bold tabular-nums">[{x1}, {y1}, {x2}, {y2}]</span>
                </div>
                {isSegmentation && inspectNode && hasDefectArea && (
                  <div className="flex justify-between border-t border-[#2B3547]/60 pt-1">
                    <span className="text-slate-400">
                      임계값 초과 픽셀 / NG 최소 면적 (원본 이미지):
                    </span>
                    <span className="text-slate-100 font-bold tabular-nums">{crop.defect_area_px} / {minimumDefectArea} px</span>
                  </div>
                )}
                {hasBlobCount && (
                  <div className="flex justify-between border-t border-[#2B3547]/60 pt-1">
                    <span className="text-slate-400">측정 Blob 개수:</span>
                    <span className="text-teal-300 font-bold tabular-nums">{crop.blob_count}</span>
                  </div>
                )}
                {hasLargestBlobArea && (
                  <div className="flex justify-between">
                    <span className="text-slate-400">최대 Blob 면적:</span>
                    <span className="text-teal-300 font-bold tabular-nums">{crop.largest_blob_area_px} px²</span>
                  </div>
                )}
                {!isFullImageSegmentation && confidence !== null && (
                  <div className="flex justify-between">
                    <span className="text-slate-400">모델 신뢰도 (Confidence):</span>
                    <span className="text-[#10B981] font-bold tabular-nums">{(confidence * 100).toFixed(1)}%</span>
                  </div>
                )}
              </div>
              {(crop.original_text !== undefined || crop.recognized_text !== undefined) && <div className="mt-3 space-y-1 rounded border border-slate-700 bg-[#0B0E14] p-3">
                <div className="flex justify-between"><span className="text-slate-400">원본 인식 문자</span><strong>{crop.original_text ?? crop.recognized_text}</strong></div>
                <div className="flex justify-between"><span className="text-slate-400">교정 후 문자</span><strong>{crop.corrected_text ?? crop.recognized_text}</strong></div>
                <p className="text-[10px] text-slate-400">{crop.correction_applied ? '문자 교정 규칙 적용' : '교정 없음'}</p>
                {!!crop.rule_violations?.length && <p className="text-rose-300">문자 규칙 위반: {crop.rule_violations.map(row => String(row.rule ?? row.kind ?? row.index ?? JSON.stringify(row))).join(', ')}</p>}
              </div>}
              {!!crop.blob_measurements?.length && <div className="mt-3 space-y-2 rounded border border-teal-800 bg-[#0B0E14] p-3">
                <h4 className="font-semibold text-teal-200">원본 클래스별 구조 측정</h4>
                {crop.blob_measurements.map(row => <div key={row.class_id} className="border-t border-slate-700 pt-1"><strong>{row.class_name} · {row.verdict}</strong><p>{row.count}개 · {row.area_px} px² · 평균 회색값 {row.mean_grayscale === null ? '측정 영역 없음' : row.mean_grayscale.toFixed(2)}</p>{!!row.violations.length && <p className="text-rose-300">범위 위반: {row.violations.join(', ')}</p>}</div>)}
              </div>}
              {!!crop.measurements?.length && <div className="mt-3 space-y-2 rounded border border-teal-800 bg-[#0B0E14] p-3">
                <h4 className="font-semibold text-teal-200">원본 좌표 길이·면적</h4>
                {crop.measurements.map(row => <div key={row.id} className="border-t border-slate-700 pt-1"><strong>{row.id} · {row.verdict}</strong><p>{row.length !== undefined ? row.length.toFixed(2) : row.area?.toFixed(2)} {row.unit.replace('2','²')}</p><p className="text-[10px] text-slate-400">{row.source_size?.join(' × ')} px · {row.measurement_source === 'source_path' ? row.interpolation === 'bezier' ? '곡선 길이' : '다각선 길이' : row.measurement_source === 'segmentation_mask' ? '클래스 마스크 면적' : '다각형 면적'}</p>{row.calibration && <p className="text-[10px] text-slate-400">교정 X {row.calibration.mm_per_pixel_x} / Y {row.calibration.mm_per_pixel_y} mm/px</p>}</div>)}
              </div>}
            </div>

            {/* Operator Quick Paging Controls & Close */}
            <div className="flex items-center justify-between pt-2 border-t border-[#2B3547]">
              <div className="flex items-center space-x-1.5">
                <button
                  disabled={currentIndex <= 0}
                  onClick={() => setInspectedCrop(crops[currentIndex - 1])}
                  className="px-2.5 py-1.5 bg-[#131822] hover:bg-[#222B3D] border border-[#2B3547] rounded text-slate-300 disabled:opacity-40 cursor-pointer transition-colors flex items-center space-x-1"
                >
                  <ChevronLeft className="w-3.5 h-3.5" />
                  <span className="text-[10px] font-mono">이전 (←)</span>
                </button>
                <span className="text-[10px] font-mono text-slate-400 px-1 tabular-nums">
                  {currentIndex + 1} / {crops.length}
                </span>
                <button
                  disabled={currentIndex >= crops.length - 1}
                  onClick={() => setInspectedCrop(crops[currentIndex + 1])}
                  className="px-2.5 py-1.5 bg-[#131822] hover:bg-[#222B3D] border border-[#2B3547] rounded text-slate-300 disabled:opacity-40 cursor-pointer transition-colors flex items-center space-x-1"
                >
                  <span className="text-[10px] font-mono">다음 (→)</span>
                  <ChevronRight className="w-3.5 h-3.5" />
                </button>
              </div>

              <button
                onClick={onClose}
                className="px-4 py-1.5 bg-[#131822] hover:bg-[#222B3D] text-slate-200 border border-[#2B3547] font-mono font-bold rounded text-xs cursor-pointer transition-colors"
              >
                닫기 (Esc)
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default CropDetailModal;
