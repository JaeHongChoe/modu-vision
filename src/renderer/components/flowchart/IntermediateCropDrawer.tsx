/**
 * src/renderer/components/flowchart/IntermediateCropDrawer.tsx
 * Inspection results from an executed pipeline.
 * High-contrast dark steel chassis (#131822 / #1A212E), strict tabular-nums font-mono,
 * master annotated vector overlay, and industrial stage latency breakdown table.
 */

import React, { useMemo, useState } from 'react';
import {
  CheckCircle2,
  Clock,
  Layers,
  Maximize2,
  Search,
  XCircle,
} from 'lucide-react';
import { useFlowchartStore } from '../../stores/useFlowchartStore';
import type { FlowchartCrop } from '../../types';

export const IntermediateCropDrawer: React.FC = () => {
  const { executionResult, setInspectedCrop } = useFlowchartStore();

  const [filter, setFilter] = useState<'all' | 'ng' | 'ok'>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [sortKey, setSortKey] = useState<'score_desc' | 'score_asc' | 'id_asc' | 'label_asc'>('score_desc');
  const [hoveredRoiId, setHoveredRoiId] = useState<string | null>(null);

  const crops: FlowchartCrop[] = executionResult?.crops ?? [];
  const executionSteps = executionResult?.execution_steps ?? [];
  const totalLatencyMs = executionResult?.total_latency_ms ?? 0;
  const isOk = executionResult?.is_ok;
  const defectiveCount = crops.filter((c) => c.verdict === 'NG').length;
  const normalCount = crops.length - defectiveCount;

  // Filter & Search & Sort
  const processedCrops = useMemo(() => {
    let result = [...crops];

    // 1. Verdict Filter
    if (filter === 'ng') result = result.filter((c) => c.verdict === 'NG');
    if (filter === 'ok') result = result.filter((c) => c.verdict === 'OK');

    // 2. Search Query
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter(
        (c) =>
          c.label.toLowerCase().includes(q) ||
          c.roi_id.toLowerCase().includes(q) ||
          c.flaw_type.toLowerCase().includes(q)
      );
    }

    // 3. Sorting
    result.sort((a, b) => {
      if (sortKey === 'score_desc') return b.defect_score - a.defect_score;
      if (sortKey === 'score_asc') return a.defect_score - b.defect_score;
      if (sortKey === 'id_asc') return a.roi_id.localeCompare(b.roi_id);
      if (sortKey === 'label_asc') return a.label.localeCompare(b.label);
      return 0;
    });

    return result;
  }, [crops, filter, searchQuery, sortKey]);

  if (!executionResult) {
    return (
      <div className="flex-1 flex items-center justify-center bg-[#0B0E14] text-slate-400 text-sm">
        실행된 검사 결과가 없습니다. 모델과 이미지를 선택한 뒤 회로를 실행하세요.
      </div>
    );
  }

  return (
    <div className="flex-1 flex p-5 space-x-5 overflow-hidden bg-[#0B0E14] text-slate-200">
      {/* =================================================================== */}
      {/* LEFT COLUMN: Master Inspection View & Stage Latency Breakdown Table */}
      {/* =================================================================== */}
      <div className="flex-1 bg-[#131822] border border-[#2B3547] rounded p-4 flex flex-col overflow-hidden">
        {/* Top Header */}
        <div className="flex items-center justify-between pb-3 border-b border-[#2B3547]">
          <div>
            <div className="flex items-center space-x-2">
              <span className="w-2 h-2 rounded-full bg-[#3B82F6]" />
              <h3 className="text-xs font-bold font-mono tracking-wider text-slate-100 uppercase">
                다중 모델 회로 검사 합성 결과 (Master Annotated View)
              </h3>
            </div>
            <p className="text-[11px] text-slate-400 mt-0.5">
              실행된 모델의 이미지와 검출 영역
            </p>
          </div>

          {/* Annunciator LED Status Badge */}
          <div
            className={`px-3 py-1.5 rounded text-xs font-mono font-bold flex items-center space-x-2 border shadow-sm ${
              isOk
                ? 'bg-[#0E2A20] text-[#10B981] border-[#10B981]/50'
                : 'bg-[#2D1216] text-[#EF4444] border-[#EF4444]/60'
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                isOk ? 'bg-[#10B981]' : 'bg-[#EF4444] animate-pulse'
              }`}
            />
            <span>최종 판정: {isOk ? 'OK' : 'NG'}</span>
          </div>
        </div>

        {/* Annotated image returned by the executed pipeline */}
        <div className="flex-1 bg-[#0B0E14] rounded border border-[#2B3547] mt-3 relative overflow-hidden flex items-center justify-center min-h-[340px]">
          {executionResult.annotated_image ? (
            <img src={executionResult.annotated_image} alt="실제 검사 결과" className="max-w-full max-h-full object-contain" />
          ) : (
            <span className="text-slate-400 text-xs">결과 이미지가 없습니다.</span>
          )}
          <div className="absolute top-2 right-2 bg-[#0B0E14]/90 border border-[#2B3547] px-2 py-1 rounded text-[10px] font-mono text-slate-300">
            {executionResult.roi_count} ROI 검사 결과
          </div>
        </div>

        {/* Rejection / Warning Strip */}
        <div
          className={`mt-2.5 px-3 py-2 rounded border text-xs font-mono flex items-center justify-between ${
            isOk
              ? 'bg-[#0E201B] border-[#10B981]/40 text-[#6EE7B7]'
              : 'bg-[#1F1317] border-[#EF4444]/50 text-[#FCA5A5]'
          }`}
        >
          <div className="flex items-center space-x-2">
            {isOk ? <CheckCircle2 className="w-4 h-4 text-[#10B981]" /> : <XCircle className="w-4 h-4 text-[#EF4444]" />}
            <span>
              판정 사유:{' '}
              <strong className="text-white">
                {executionResult.rejection_reason || '판정 사유가 제공되지 않았습니다.'}
              </strong>
            </span>
          </div>
          <span className="text-[10px] text-slate-400">로컬 모델 결과</span>
        </div>

        {/* =================================================================== */}
        {/* Stage Latency Breakdown Table (Industrial Instrumentation) */}
        {/* =================================================================== */}
        <div className="mt-3 bg-[#0B0E14] border border-[#2B3547] rounded p-2.5">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center space-x-2">
              <Clock className="w-3.5 h-3.5 text-[#3B82F6]" />
              <h4 className="text-[11px] font-bold font-mono uppercase text-slate-200">
                실행 단계별 지연 시간
              </h4>
            </div>
            <div className="text-[11px] font-mono">
              <span className="text-slate-400">로컬 실행 시간: </span>
              <span className="text-white font-bold tabular-nums">{totalLatencyMs.toFixed(1)} ms</span>
            </div>
          </div>

          <table className="w-full text-left text-[11px] font-mono border-collapse">
            <thead>
              <tr className="border-b border-[#2B3547] text-slate-400 text-[10px] uppercase">
                <th className="py-1 px-2">단계 (Stage)</th>
                <th className="py-1 px-2">공정 모듈 (Module)</th>
                <th className="py-1 px-2">상태 (Status)</th>
                <th className="py-1 px-2 text-right">소요시간 (ms)</th>
                <th className="py-1 px-2 w-36">점유율 (Takt %)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1A212E]">
              {executionSteps.map((step, idx) => {
                const stepLat = step.latency_ms ?? 0;
                const ratio = totalLatencyMs > 0 ? Math.min(100, (stepLat / totalLatencyMs) * 100) : 0;
                const isStepNg = step.status === 'flagged_ng';

                return (
                  <tr key={step.node_id} className="hover:bg-[#131822] transition-colors">
                    <td className="py-1 px-2 text-slate-400 font-bold tabular-nums">
                      {String(idx + 1).padStart(2, '0')}
                    </td>
                    <td className="py-1 px-2 text-slate-200 font-medium">{step.name}</td>
                    <td className="py-1 px-2">
                      <span
                        className={`inline-flex items-center space-x-1 px-1.5 py-0.5 rounded text-[9px] font-bold ${
                          isStepNg
                            ? 'bg-[#2D1216] text-[#EF4444] border border-[#EF4444]/40'
                            : 'bg-[#0E2A20] text-[#10B981] border border-[#10B981]/40'
                        }`}
                      >
                        <span className={`w-1.5 h-1.5 rounded-full ${isStepNg ? 'bg-[#EF4444]' : 'bg-[#10B981]'}`} />
                        <span>{step.status}</span>
                      </span>
                    </td>
                    <td className="py-1 px-2 text-right font-bold text-slate-100 tabular-nums">
                      {stepLat.toFixed(1)} <span className="text-slate-400 text-[9px]">ms</span>
                    </td>
                    <td className="py-1 px-2">
                      <div className="flex items-center space-x-2">
                        <div className="flex-1 bg-[#1A212E] h-1.5 rounded border border-[#2B3547] overflow-hidden">
                          <div
                            className={`h-full rounded ${isStepNg ? 'bg-[#EF4444]' : 'bg-[#3B82F6]'}`}
                            style={{ width: `${ratio}%` }}
                          />
                        </div>
                        <span className="text-[10px] text-slate-400 tabular-nums w-8 text-right">
                          {ratio.toFixed(0)}%
                        </span>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* =================================================================== */}
      {/* RIGHT COLUMN: Real crop gallery */}
      {/* =================================================================== */}
      <div className="w-[460px] bg-[#131822] border border-[#2B3547] rounded p-4 flex flex-col overflow-hidden">
        {/* Gallery Header */}
        <div className="flex items-center justify-between pb-3 border-b border-[#2B3547]">
          <div className="flex items-center space-x-2">
            <Layers className="w-4 h-4 text-[#3B82F6]" />
            <h3 className="text-xs font-bold font-mono uppercase tracking-wider text-slate-100">
              추출 부품별 정밀 검사 ({crops.length}개 ROI)
            </h3>
          </div>

          {/* Filter Tabs */}
          <div className="flex bg-[#0B0E14] p-0.5 rounded border border-[#2B3547] text-[10px] font-mono font-bold">
            <button
              onClick={() => setFilter('all')}
              className={`px-2 py-0.5 rounded cursor-pointer transition-colors ${
                filter === 'all' ? 'bg-[#2B3547] text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              전체 ({crops.length})
            </button>
            <button
              onClick={() => setFilter('ng')}
              className={`px-2 py-0.5 rounded cursor-pointer transition-colors ${
                filter === 'ng' ? 'bg-[#EF4444] text-white' : 'text-slate-400 hover:text-[#EF4444]'
              }`}
            >
              NG ({defectiveCount})
            </button>
            <button
              onClick={() => setFilter('ok')}
              className={`px-2 py-0.5 rounded cursor-pointer transition-colors ${
                filter === 'ok' ? 'bg-[#10B981] text-slate-950 font-bold' : 'text-slate-400 hover:text-[#10B981]'
              }`}
            >
              OK ({normalCount})
            </button>
          </div>
        </div>

        {/* Gallery Sub-Toolbar (Search & Sort) */}
        <div className="py-2.5 flex space-x-2">
          {/* Search Input */}
          <div className="flex-1 relative">
            <Search className="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-2" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="부품명 / 결함 분류 검색..."
              className="w-full bg-[#0B0E14] border border-[#2B3547] rounded pl-8 pr-2.5 py-1 text-xs font-mono text-slate-100 placeholder-slate-500 focus:outline-none focus:border-slate-400"
            />
          </div>

          {/* Sort Selector */}
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as any)}
            className="bg-[#0B0E14] border border-[#2B3547] rounded px-2 py-1 text-xs font-mono text-slate-300 focus:outline-none"
          >
            <option value="score_desc">점수 높은순</option>
            <option value="score_asc">점수 낮은순</option>
            <option value="id_asc">ROI 번호순</option>
            <option value="label_asc">부품명순</option>
          </select>
        </div>

        {/* ROI Cards List */}
        <div className="flex-1 overflow-y-auto space-y-2 pr-1 custom-scrollbar">
          {processedCrops.length === 0 ? (
            <div className="text-center py-20 text-slate-400 text-xs font-mono">
              일치하는 ROI 크롭이 없습니다.
            </div>
          ) : (
            processedCrops.map((crop) => {
              const isNg = crop.verdict === 'NG';
              const isHovered = hoveredRoiId === crop.roi_id;
              const [x1, y1, x2, y2] = crop.bbox;
              const w = x2 - x1;
              const h = y2 - y1;

              return (
                <div
                  key={crop.roi_id}
                  onClick={() => setInspectedCrop(crop)}
                  onMouseEnter={() => setHoveredRoiId(crop.roi_id)}
                  onMouseLeave={() => setHoveredRoiId(null)}
                  className={`p-2.5 rounded border transition-colors cursor-pointer flex space-x-3 items-center ${
                    isNg
                      ? 'border-l-4 border-l-[#EF4444] border-t-[#2B3547] border-r-[#2B3547] border-b-[#2B3547] bg-[#1E1417]/50 hover:bg-[#28181D]'
                      : 'border-l-4 border-l-[#10B981] border-t-[#2B3547] border-r-[#2B3547] border-b-[#2B3547] bg-[#121E1C]/40 hover:bg-[#162724]'
                  } ${isHovered ? 'ring-1 ring-[#F59E0B]/70' : ''}`}
                >
                  {/* Thumbnail Frame */}
                  <div className="w-16 h-16 rounded bg-[#0B0E14] border border-[#2B3547] overflow-hidden flex items-center justify-center flex-shrink-0 relative">
                    <img
                      src={crop.crop_thumbnail}
                      alt={crop.label}
                      className="w-full h-full object-cover"
                    />
                    <div className="absolute bottom-0 right-0 bg-[#0B0E14]/90 px-1 text-[8px] font-mono text-slate-300">
                      {w}×{h}
                    </div>
                  </div>

                  {/* Component Info & Diagnostics */}
                  <div className="flex-1 min-w-0 text-xs">
                    {/* Top Row: Label & Verdict Badge */}
                    <div className="flex items-center justify-between">
                      <span className="font-mono font-bold text-slate-100 truncate">
                        {crop.label}
                      </span>
                      <span
                        className={`text-[9px] font-mono font-bold px-1.5 py-0.5 rounded ${
                          isNg ? 'bg-[#EF4444] text-white' : 'bg-[#10B981] text-slate-950'
                        }`}
                      >
                        {crop.verdict}
                      </span>
                    </div>

                    {/* Defect Score Readout & Mini-Gauge */}
                    <div className="mt-1 flex items-center justify-between text-[11px] font-mono">
                      <span className="text-slate-400">결함 점수:</span>
                      <div className="flex items-center space-x-1.5">
                        <span className={`font-bold tabular-nums ${isNg ? 'text-[#EF4444]' : 'text-[#10B981]'}`}>
                          {(crop.defect_score * 100).toFixed(1)}%
                        </span>
                        <span className="text-slate-400 text-[9px] tabular-nums">
                          ({crop.defect_score.toFixed(3)})
                        </span>
                      </div>
                    </div>

                    {/* Flaw Type */}
                    <div className="text-[10px] text-slate-300 truncate mt-0.5">
                      {crop.flaw_type}
                    </div>

                    {/* Coordinates & Expand Action */}
                    <div className="mt-1 flex items-center justify-between text-[9px] font-mono text-slate-400 border-t border-[#2B3547]/60 pt-1">
                      <span className="tabular-nums">
                        X:{x1} Y:{y1} W:{w} H:{h} px
                      </span>
                      <span className="text-[#3B82F6] flex items-center space-x-0.5 hover:underline">
                        <Maximize2 className="w-2.5 h-2.5" />
                        <span>정밀 진단 ↗</span>
                      </span>
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
};

export default IntermediateCropDrawer;
