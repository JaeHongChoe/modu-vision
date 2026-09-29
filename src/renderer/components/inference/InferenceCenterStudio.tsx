/**
 * src/renderer/components/inference/InferenceCenterStudio.tsx
 * Stage 6: Inference Center & Production Runtime Export (Neuro-R Style).
 * Adheres to Keyence & Cognex Industrial Deployment Standards:
 * - Keyence Digital Instrument Bay: High-contrast tabular-nums digital readouts
 * - Real-time FPS gauge & PPM throughput
 * - Cycle Time Limit Gauge Bar with line-speed threshold marker (25.0 ms)
 * - Jitter indicator (±1-sigma) & P95 tail latency gauge
 * - Physical LED Line Readiness Annunciator
 * - Standalone Neuro-R Package Manifest & tabbed C#/C++/Python SDK Code Inspector
 * - Zero diffuse glows, zero optical blurs, zero gradients, 1px precision borders
 */

import React, { useState } from 'react';
import {
  Check,
  CheckCircle2,
  Code2,
  Copy,
  Gauge,
  Package,
  Server,
  Sliders,
  Zap,
} from 'lucide-react';
import { useEvaluationStore } from '../../stores/useEvaluationStore';
import { useTrainingStore } from '../../stores/useTrainingStore';
import { useProjectStore } from '../../stores/useProjectStore';
import { api } from '../../services/api';
import { OperatorGuidanceBanner } from '../common/OperatorGuidanceBanner';
import { LedAnnunciator } from '../common/LedAnnunciator';
import type { RuntimeExportResult } from '../../types';

export const InferenceCenterStudio: React.FC = () => {
  const { language, backendStatus } = useProjectStore();
  const { benchmarkResult, isBenchmarking, runBenchmark } = useEvaluationStore();
  const jobId = useTrainingStore((state) => state.jobId);

  const [activeCodeTab, setActiveCodeTab] = useState<'csharp' | 'cpp' | 'python'>('python');
  const [isExporting, setIsExporting] = useState(false);
  const [exportResult, setExportResult] = useState<RuntimeExportResult | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [maxTaktLimit, setMaxTaktLimit] = useState<number>(25.0); // Line speed threshold limit in ms
  const [exportFormat, setExportFormat] = useState<'onnx' | 'torchscript'>('onnx');
  const quantizeFp16 = false;
  const [resolution, setResolution] = useState<number>(256);

  const isKo = language === 'ko';

  const handleBenchmark = async () => {
    setActionError(null);
    if (!jobId) {
      setActionError('학습을 완료한 모델을 먼저 선택하세요.');
      return;
    }
    await runBenchmark(25, resolution);
  };

  const handleExport = async () => {
    setActionError(null);
    if (!jobId) {
      setActionError('학습을 완료한 모델을 먼저 선택하세요.');
      return;
    }
    setIsExporting(true);
    try {
      const res = await api.export.runtime({
        job_id: jobId,
        package_name: `modu_vision_${jobId}`,
        export_format: exportFormat,
        resolution,
        quantize_fp16: quantizeFp16,
      });
      setExportResult(res);
      setIsExporting(false);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : '내보내기 실패');
      setIsExporting(false);
    }
  };

  // Telemetry Metrics
  const fps = benchmarkResult?.fps ?? 0;
  const meanLatency = benchmarkResult?.mean_latency_ms ?? 0;
  const p95Latency = benchmarkResult?.p95_latency_ms ?? 0;
  const minLatency = benchmarkResult?.min_latency_ms ?? 0;
  const maxLatency = benchmarkResult?.max_latency_ms ?? 0;
  const stdLatency = benchmarkResult?.std_latency_ms ?? 0;
  const ppm = Math.round(fps * 60);

  // Line Readiness Status Calculation
  const isLineReady = Boolean(benchmarkResult) && meanLatency <= maxTaktLimit && p95Latency <= maxTaktLimit * 1.25;
  const headroomPct = benchmarkResult ? Number((((maxTaktLimit - meanLatency) / maxTaktLimit) * 100).toFixed(1)) : 0;

  // Gauge bar scaling (0 to max(60, maxTaktLimit * 1.6))
  const gaugeMaxMs = Math.max(60.0, maxTaktLimit * 1.6);
  const actualFillPct = Math.min(100, Math.max(0, (meanLatency / gaugeMaxMs) * 100));
  const thresholdMarkerPct = Math.min(100, Math.max(0, (maxTaktLimit / gaugeMaxMs) * 100));

  // Only the generated infer.py is a supported client. Native SDKs need parity tests.
  const activeSnippet = activeCodeTab === 'python'
    ? `# Run the infer.py shipped in the exported package.
# It reads config.json and loads model.onnx or model.pt.
python infer.py --image /path/to/inspection_image.jpg
python infer.py --self-test`
    : `# Native ${activeCodeTab === 'csharp' ? 'C#' : 'C++'} client is unavailable.
# Use the verified infer.py in the package, or integrate the model
# after preprocessing and output parity tests against it.`;

  const codeLines = activeSnippet.split('\n');

  const handleCopyCode = () => {
    navigator.clipboard.writeText(activeSnippet);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-[#0B0E14] text-slate-200 overflow-y-auto select-none p-5 space-y-5 font-sans">
      {/* Top Operator Guidance Banner */}
      <OperatorGuidanceBanner step={6} />

      {/* Title & Action Strip */}
      <div className="flex items-center justify-between pb-3 border-b border-[#2B3547] bg-[#131822] -mx-5 -mt-5 p-4 border-t-0">
        <div>
          <div className="flex items-center space-x-2.5">
            <Server className="w-5 h-5 text-slate-300" />
            <h2 className="text-sm font-bold text-slate-100 uppercase tracking-wider font-mono">
              {isKo ? '인퍼런스 센터 및 모델 내보내기' : 'Inference Center & Model Export'}
            </h2>
            <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-[#1A212E] text-slate-300 border border-[#2B3547]">
              STAGE 6
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-0.5">
            {isKo
              ? '합성 입력으로 모델 추론 속도를 측정하고 Python 독립 실행 패키지를 내보냅니다. 생산 라인과 PLC 연동은 별도 검증이 필요합니다.'
              : 'Benchmark model inference with synthetic input and export a standalone Python package.'}
          </p>
        </div>

        {/* Global Trigger Actions */}
        <div className="flex items-center space-x-3">
          <button
            onClick={handleBenchmark}
            disabled={isBenchmarking}
            className="flex items-center space-x-2 px-3.5 py-2 bg-[#1A212E] hover:bg-[#2B3547] active:bg-[#0B0E14] border border-[#2B3547] rounded text-xs font-semibold cursor-pointer transition-colors text-slate-200"
          >
            <Gauge className={`w-4 h-4 text-slate-300 ${isBenchmarking ? 'animate-spin' : ''}`} />
            <span>
              {isBenchmarking
                ? isKo
                  ? '벤치마크 실측 중...'
                  : 'Benchmarking...'
                : isKo
                ? '추론 속도 벤치마크 실행'
                : 'Run Benchmark'}
            </span>
          </button>

          <button
            onClick={handleExport}
            disabled={isExporting}
            className="flex items-center space-x-2 px-4 py-2 bg-[#10B981] hover:bg-[#059669] active:bg-[#047857] text-slate-950 font-bold rounded border border-[#10B981] transition-colors cursor-pointer text-xs"
          >
            <Package className="w-4 h-4" />
            <span>
              {isExporting
                ? isKo
                  ? '패키징 생성 중...'
                  : 'Exporting...'
                : isKo
                ? 'Python 런타임 패키지 내보내기'
                : 'Export Runtime Package'}
            </span>
          </button>
        </div>
      </div>

      {actionError && <div role="alert" className="rounded border border-amber-600 bg-amber-950/40 p-2 text-xs text-amber-200">{actionError}</div>}

      {/* Main Grid: 2 Column Bay */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* Left Column: Keyence-Style Precision Digital Instrument Panel */}
        <div className="bg-[#131822] border border-[#2B3547] rounded p-4 flex flex-col space-y-4">
          <div className="flex items-center justify-between pb-2.5 border-b border-[#2B3547]">
            <h3 className="text-xs font-bold text-slate-200 uppercase tracking-wider font-mono flex items-center space-x-2">
              <Zap className="w-4 h-4 text-amber-400" />
              <span>{isKo ? '실시간 하드웨어 가속 계측 패널' : 'Hardware Telemetry Instruments'}</span>
            </h3>
            <span className="text-[11px] text-slate-300 font-mono bg-[#1A212E] px-2.5 py-0.5 rounded border border-[#2B3547]">
              {benchmarkResult?.device_name || backendStatus.deviceName || 'Unknown device'}
            </span>
          </div>

          {/* 3 Discrete Digital Meters */}
          <div className="grid grid-cols-3 gap-3">
            {/* METER 1: FPS */}
            <div className="p-3 bg-[#0B0E14] border border-[#1F2737] rounded flex flex-col justify-between">
              <div className="text-[10px] text-slate-400 uppercase font-mono font-semibold">
                검사 속도 (FPS)
              </div>
              <div className="text-2xl lg:text-3xl font-bold font-mono tabular-nums text-emerald-400 my-1">
                {benchmarkResult ? fps : '—'} <span className="text-xs font-normal text-slate-500">FPS</span>
              </div>
              <div className="text-[10px] text-slate-400 font-mono tabular-nums">
                {benchmarkResult ? ppm.toLocaleString() : '—'} <span className="text-[9px] text-slate-500">inference/min estimate</span>
              </div>
            </div>

            {/* METER 2: Mean Takt Time */}
            <div className="p-3 bg-[#0B0E14] border border-[#1F2737] rounded flex flex-col justify-between">
              <div className="text-[10px] text-slate-400 uppercase font-mono font-semibold">
                평균 택트 타임
              </div>
              <div className="text-2xl lg:text-3xl font-bold font-mono tabular-nums text-slate-100 my-1">
                {benchmarkResult ? meanLatency : '—'} <span className="text-xs font-normal text-slate-500">ms</span>
              </div>
              <div className="text-[10px] text-slate-400 font-mono tabular-nums">
                Min: {benchmarkResult ? minLatency : '—'}ms | Max: {benchmarkResult ? maxLatency : '—'}ms
              </div>
            </div>

            {/* METER 3: Jitter & P95 */}
            <div className="p-3 bg-[#0B0E14] border border-[#1F2737] rounded flex flex-col justify-between">
              <div className="text-[10px] text-slate-400 uppercase font-mono font-semibold">
                P95 지연 및 지터
              </div>
              <div className="text-2xl lg:text-3xl font-bold font-mono tabular-nums text-slate-100 my-1">
                {benchmarkResult ? p95Latency : '—'} <span className="text-xs font-normal text-slate-500">ms</span>
              </div>
              <div className="text-[10px] text-cyan-400 font-mono font-semibold tabular-nums">
                지터: ±{benchmarkResult ? stdLatency : '—'} ms (1σ)
              </div>
            </div>
          </div>

          {/* Cycle Time Limit Gauge Bar with Threshold Marker */}
          <div className="p-3 bg-[#1A212E] border border-[#2B3547] rounded space-y-2">
            <div className="flex items-center justify-between text-xs font-mono">
              <div className="flex items-center space-x-2">
                <Sliders className="w-3.5 h-3.5 text-slate-400" />
                <span className="font-bold text-slate-200">공정 허용 사이클 타임 한계 (Line Takt Limit)</span>
              </div>
              <div className="flex items-center space-x-1.5">
                {[15.0, 25.0, 50.0].map((limit) => (
                  <button
                    key={limit}
                    onClick={() => setMaxTaktLimit(limit)}
                    className={`px-2 py-0.5 rounded text-[10px] font-mono cursor-pointer transition-colors ${
                      maxTaktLimit === limit
                        ? 'bg-[#10B981] text-slate-950 font-bold border border-[#10B981]'
                        : 'bg-[#0B0E14] text-slate-400 hover:text-white border border-[#2B3547]'
                    }`}
                  >
                    {limit.toFixed(1)}ms
                  </button>
                ))}
              </div>
            </div>

            {/* Gauge Bar */}
            <div className="relative h-6 bg-[#0B0E14] rounded border border-[#2B3547] overflow-hidden">
              {/* Actual Latency Fill */}
              <div
                className={`h-full transition-all duration-300 ${
                  meanLatency <= maxTaktLimit * 0.8
                    ? 'bg-[#10B981]'
                    : meanLatency <= maxTaktLimit
                    ? 'bg-[#F59E0B]'
                    : 'bg-[#EF4444]'
                }`}
                style={{ width: `${actualFillPct}%` }}
              />

              {/* Threshold Marker Pin */}
              <div
                className="absolute top-0 bottom-0 w-[2px] bg-[#EF4444] z-10"
                style={{ left: `${thresholdMarkerPct}%` }}
              >
                <div className="absolute -top-1 -left-2 text-[8px] font-mono font-bold bg-[#EF4444] text-white px-1 rounded-sm">
                  ▲
                </div>
              </div>

              {/* In-bar text readouts */}
              <div className="absolute inset-0 flex items-center justify-between px-2 text-[10px] font-mono font-bold select-none pointer-events-none">
                <span className="text-slate-950 mix-blend-difference tabular-nums">
                  실측: {benchmarkResult ? meanLatency : '—'} ms
                </span>
                <span className="text-slate-400 tabular-nums">
                  한계: {maxTaktLimit.toFixed(1)} ms
                </span>
              </div>
            </div>

            {/* Headroom / Buffer status */}
            <div className="flex items-center justify-between text-[11px] font-mono text-slate-400 pt-0.5">
              <span>
                공정 여유 마진 (Safety Headroom):{' '}
                <span className={`font-bold tabular-nums ${headroomPct >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                  {benchmarkResult ? (headroomPct >= 0 ? `+${headroomPct}%` : `${headroomPct}% (초과)`) : '미측정'}
                </span>
              </span>
              <span className="tabular-nums">
                최대 스케일: {gaugeMaxMs.toFixed(0)} ms
              </span>
            </div>
          </div>

          {/* PASS / FAIL Line Readiness Annunciator */}
          <div
            className={`p-3 rounded border transition-colors flex items-start space-x-3 ${
              isLineReady
                ? 'bg-[#0D1C16] border-[#10B981]/60 text-emerald-200'
                : 'bg-[#1A0E11] border-[#EF4444]/60 text-rose-200'
            }`}
          >
            <LedAnnunciator state={!benchmarkResult ? 'standby' : isLineReady ? 'pass' : 'fail'} size="md" />
            <div className="space-y-0.5 flex-1">
              <div className="flex items-center justify-between">
                <h4 className="text-xs font-bold font-mono tracking-tight text-slate-100">
                  {!benchmarkResult ? '모델 속도 미측정' : isLineReady
                    ? '모델 추론 속도 목표 충족'
                    : '모델 추론 속도 목표 미달'}
                </h4>
                <span className="text-[10px] font-mono uppercase font-bold px-2 py-0.5 rounded border bg-[#0B0E14] text-slate-300 border-[#2B3547]">
                  {!benchmarkResult ? 'UNTESTED' : isLineReady ? 'BENCHMARK PASS' : 'BENCHMARK FAIL'}
                </span>
              </div>
              <p className="text-[11px] text-slate-300 leading-relaxed font-sans">
                {!benchmarkResult ? '학습 모델로 벤치마크를 실행해야 속도 수치를 표시합니다.' : isLineReady
                  ? `합성 입력에서 모델 추론 지연시간 ${meanLatency}ms를 측정했습니다. 실제 카메라·PLC 통합 검사는 별도 필요합니다.`
                  : `합성 입력에서 모델 추론 지연시간 ${meanLatency}ms가 목표 ${maxTaktLimit}ms를 초과했습니다.`}
              </p>
            </div>
          </div>
        </div>

        {/* Right Column: Neuro-R Standalone Package Manifest */}
        <div className="bg-[#131822] border border-[#2B3547] rounded p-4 flex flex-col space-y-4">
          <div className="flex items-center justify-between pb-2.5 border-b border-[#2B3547]">
            <h3 className="text-xs font-bold text-slate-200 uppercase tracking-wider font-mono flex items-center space-x-2">
              <Package className="w-4 h-4 text-emerald-400" />
              <span>{isKo ? '독립 실행 패키지 아티팩트' : 'Standalone Package Manifest'}</span>
            </h3>
            <span className="text-[10px] font-mono text-slate-400 tabular-nums">
              {exportResult?.total_files ?? 0} FILES INDEXED
            </span>
          </div>

          {/* Export Parameters Strip */}
          <div className="p-3 bg-[#1A212E] border border-[#2B3547] rounded flex items-center justify-between text-xs font-mono">
            <div className="flex items-center space-x-4">
              <div className="flex items-center space-x-2">
                <span className="text-slate-400">포맷:</span>
                <select
                  value={exportFormat}
                  onChange={(e) => setExportFormat(e.target.value as any)}
                  className="bg-[#0B0E14] border border-[#2B3547] rounded px-2 py-1 text-slate-200 text-xs font-mono focus:outline-none"
                >
                  <option value="onnx">ONNX (Cross-Platform)</option>
                  <option value="torchscript">TorchScript (.pt)</option>
                </select>
              </div>

              <div className="flex items-center space-x-2">
                <span className="text-slate-400">해상도:</span>
                <select
                  value={resolution}
                  onChange={(e) => setResolution(Number(e.target.value))}
                  className="bg-[#0B0E14] border border-[#2B3547] rounded px-2 py-1 text-slate-200 text-xs font-mono focus:outline-none"
                >
                  <option value={224}>224 x 224</option>
                  <option value={256}>256 x 256 (권장)</option>
                  <option value={512}>512 x 512</option>
                </select>
              </div>

              <label className="flex items-center space-x-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={quantizeFp16}
                  disabled
                  className="rounded border-[#2B3547] bg-[#0B0E14] text-emerald-500 focus:ring-0"
                />
                <span className="text-slate-400 text-xs">FP16 변환 미지원</span>
              </label>
            </div>
          </div>

          {/* File Manifest List */}
          <div className="flex-1 bg-[#0B0E14] border border-[#1F2737] rounded p-3 overflow-y-auto space-y-1.5">
            {!exportResult && <p className="text-xs text-slate-400">내보낸 패키지가 없습니다.</p>}
            {(exportResult?.manifest || []).map((file) => (
              <div
                key={file.name}
                className="flex items-center justify-between p-2 bg-[#131822] hover:bg-[#1A212E] rounded border border-[#2B3547] text-xs font-mono transition-colors"
              >
                <div className="flex items-center space-x-2.5">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                  <span className="font-bold text-slate-200">{file.name}</span>
                </div>
                <div className="flex items-center space-x-3">
                  <span className="text-slate-400 tabular-nums">{file.size_kb.toFixed(1)} KB</span>
                  <span className="text-[10px] text-emerald-400 font-bold px-1.5 py-0.5 rounded bg-[#0D1C16] border border-[#10B981]/40">
                    CREATED
                  </span>
                </div>
              </div>
            ))}
          </div>

          {/* Package Storage Path */}
          <div className="text-[11px] text-slate-400 bg-[#0B0E14] p-2.5 rounded border border-[#1F2737] truncate font-mono">
            저장 경로: <span className="text-slate-200 font-bold">{exportResult?.package_path || '—'}</span>
          </div>
        </div>
      </div>

      {/* Bottom Section: Client SDK Code Inspector */}
      <div className="bg-[#131822] border border-[#2B3547] rounded p-4 flex flex-col space-y-3">
        <div className="flex items-center justify-between pb-2 border-b border-[#2B3547]">
          <div className="flex items-center space-x-2">
            <Code2 className="w-4 h-4 text-slate-300" />
            <h3 className="text-xs font-bold text-slate-200 font-mono uppercase tracking-wider">
              {isKo
                ? '패키지 사용 예시 및 지원 범위'
                : 'Package Usage and Supported Clients'}
            </h3>
          </div>

          <div className="flex items-center space-x-3">
            {/* Language Tabs */}
            <div className="flex bg-[#0B0E14] p-0.5 rounded border border-[#2B3547] text-xs font-mono">
              <button
                onClick={() => setActiveCodeTab('csharp')}
                className={`px-3 py-1 rounded cursor-pointer transition-colors font-semibold ${
                  activeCodeTab === 'csharp'
                    ? 'bg-[#1A212E] text-slate-100 border border-[#2B3547]'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                C# (.NET 8 / WPF)
              </button>
              <button
                onClick={() => setActiveCodeTab('cpp')}
                className={`px-3 py-1 rounded cursor-pointer transition-colors font-semibold ${
                  activeCodeTab === 'cpp'
                    ? 'bg-[#1A212E] text-slate-100 border border-[#2B3547]'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                C++ (OpenCV DNN)
              </button>
              <button
                onClick={() => setActiveCodeTab('python')}
                className={`px-3 py-1 rounded cursor-pointer transition-colors font-semibold ${
                  activeCodeTab === 'python'
                    ? 'bg-[#1A212E] text-slate-100 border border-[#2B3547]'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                Python (ONNXRuntime)
              </button>
            </div>

            {/* Copy Button */}
            <button
              onClick={handleCopyCode}
              className="flex items-center space-x-1.5 px-3 py-1.5 bg-[#1A212E] hover:bg-[#2B3547] text-slate-200 rounded border border-[#2B3547] text-xs font-mono cursor-pointer transition-colors"
            >
              {copied ? (
                <>
                  <Check className="w-3.5 h-3.5 text-emerald-400" />
                  <span className="text-emerald-400 font-bold">복사 완료!</span>
                </>
              ) : (
                <>
                  <Copy className="w-3.5 h-3.5 text-slate-400" />
                  <span>코드 복사</span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* IDE-Grade Numbered Code Gutter Block */}
        <div className="flex bg-[#05070A] rounded border border-[#1F2737] overflow-hidden">
          {/* Line Numbers Gutter */}
          <div className="w-12 bg-[#080B10] border-r border-[#1F2737] text-slate-500 font-mono text-xs select-none pr-3 py-4 text-right leading-relaxed shrink-0">
            {codeLines.map((_, i) => (
              <div key={i}>{i + 1}</div>
            ))}
          </div>

          {/* Syntax Code Content */}
          <pre className="flex-1 p-4 font-mono text-xs text-slate-200 overflow-x-auto leading-relaxed select-text">
            {activeSnippet}
          </pre>
        </div>
      </div>
    </div>
  );
};

export default InferenceCenterStudio;
