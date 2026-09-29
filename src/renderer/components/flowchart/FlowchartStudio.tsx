/**
 * src/renderer/components/flowchart/FlowchartStudio.tsx
 * Stage 5: Multi-Model Chaining & Flowchart Studio (Cognex / Keyence Standards).
 * Features:
 *  - 2D DAG Circuit Canvas with 32px PCB Grid.
 *  - Integrated DAGCircuitOverlay (45° chamfered traces + dual-branching).
 *  - Strict Cognex Deep Steel Theme (#0B0E14, #131822, #1A212E, #2B3547).
 *  - Anti-AI Clean Design: Zero gradients, zero diffuse shadows, zero backdrop blur.
 *  - Docked Right Property Inspector with strict tabular-nums precision inputs.
 */

import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  AlertTriangle,
  GitFork,
  Image as ImageIcon,
  Play,
  RotateCcw,
  Save,
  Sliders,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import { useFlowchartStore } from '../../stores/useFlowchartStore';
import { useEvaluationStore } from '../../stores/useEvaluationStore';
import { useProjectStore } from '../../stores/useProjectStore';
import { useTrainingStore } from '../../stores/useTrainingStore';
import { resolveApiUrl } from '../../services/api';
import { CustomNode } from './CustomNode';
import { DAGCircuitOverlay } from './DAGCircuitOverlay';
import { ImagePickerModal } from './ImagePickerModal';
import { IntermediateCropDrawer } from './IntermediateCropDrawer';
import { CropDetailModal } from './CropDetailModal';
import { computeFlowchartViewport } from './flowchartViewport';

export const FlowchartStudio: React.FC = () => {
  const { language, task } = useProjectStore();
  const { jobId: trainedJobId, status: trainingStatus, isCurrentData } = useTrainingStore();
  const evaluatedJobId = useEvaluationStore((state) => state.jobId);
  const segmentationJobId = task === 'segmentation'
    ? (isCurrentData && trainingStatus === 'completed' ? trainedJobId : null) || evaluatedJobId
    : null;
  const {
    pipeline,
    pipelineDirty,
    modelContextInvalidated,
    contextRevision,
    executionResult,
    isLoading,
    isRunning,
    activeRunningNodeId,
    selectedNodeId,
    saveMessage,
    errorMessage,
    selectedImage,
    isImagePickerOpen,
    inspectedCrop,
    loadPipeline,
    loadSingleSegmentationTemplate,
    savePipeline,
    runPipeline,
    selectNode,
    updateNodeData,
    setImagePickerOpen,
    setInspectedCrop,
    clearError,
  } = useFlowchartStore();

  const [activeTab, setActiveTab] = useState<'flow' | 'results'>('flow');
  const [zoomLevel, setZoomLevel] = useState<number>(1.0);
  const canvasRef = useRef<HTMLDivElement>(null);
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });

  useLayoutEffect(() => {
    if (activeTab !== 'flow' || !canvasRef.current) return;
    const canvas = canvasRef.current;
    const measure = () => {
      const rect = canvas.getBoundingClientRect();
      setCanvasSize((previous) =>
        previous.width === rect.width && previous.height === rect.height
          ? previous
          : { width: rect.width, height: rect.height }
      );
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [activeTab]);

  useEffect(() => {
    let cancelled = false;
    const openFlow = async () => {
      if (modelContextInvalidated) {
        const current = useFlowchartStore.getState().pipeline;
        if (!current) {
          await loadSingleSegmentationTemplate(segmentationJobId || undefined);
          return;
        }
        const singleInspection = current.id === 'single_segmentation'
          ? current.nodes.find((node) => node.data.node_type === 'inspection')
          : null;
        if (segmentationJobId && singleInspection && !singleInspection.data.model_job_id) {
          updateNodeData(singleInspection.id, { model_job_id: segmentationJobId });
        }
        return;
      }
      const loaded = await loadPipeline();
      if (cancelled || !loaded) return;
      const modelNodes = loaded.nodes.filter((node) =>
        node.data.node_type === 'inspection' || node.data.node_type === 'detection_crop'
      );
      const singleInspection = loaded.id === 'single_segmentation'
        ? modelNodes.find((node) => node.data.node_type === 'inspection')
        : null;
      if (segmentationJobId && singleInspection && !singleInspection.data.model_job_id) {
        updateNodeData(singleInspection.id, { model_job_id: segmentationJobId });
        return;
      }
      if (
        segmentationJobId &&
        !useFlowchartStore.getState().pipelineDirty && modelNodes.some((node) => !node.data.model_job_id)
      ) {
        await loadSingleSegmentationTemplate(segmentationJobId);
      }
    };
    openFlow().catch(() => {});
    return () => { cancelled = true; };
  }, [loadPipeline, loadSingleSegmentationTemplate, segmentationJobId, updateNodeData,
    modelContextInvalidated, contextRevision]);

  const handleRun = async () => {
    if (await runPipeline()) setActiveTab('results');
  };

  const handleSingleModel = async () => {
    await loadSingleSegmentationTemplate(segmentationJobId || undefined);
    setActiveTab('flow');
  };

  const handleRestoreSaved = async () => {
    if (pipelineDirty && !window.confirm('현재 플로우의 저장하지 않은 변경 사항을 버리고 저장본을 불러올까요?')) return;
    await loadPipeline(true);
    setActiveTab('flow');
  };

  const selectedNode = pipeline?.nodes.find((n) => n.id === selectedNodeId);
  const needsModel = pipeline?.nodes.some((node) =>
    (node.data.node_type === 'inspection' || node.data.node_type === 'detection_crop') && !node.data.model_job_id
  ) ?? true;

  // Normalize node positions if not set
  const getNodePosition = (nodeId: string, idx: number, currentPos?: { x: number; y: number }) => {
    if (currentPos && (currentPos.x > 0 || currentPos.y > 0)) {
      return currentPos;
    }
    switch (nodeId) {
      case 'node_input':
        return { x: 40, y: 160 };
      case 'node_crop':
        return { x: 340, y: 160 };
      case 'node_inspect':
        return { x: 640, y: 160 };
      case 'node_decision':
        return { x: 940, y: 160 };
      case 'node_output_pass':
        return { x: 1260, y: 100 };
      case 'node_output_ng':
        return { x: 1260, y: 260 };
      case 'node_output':
        return { x: 1260, y: 160 };
      default:
        return { x: 40 + idx * 300, y: 160 };
    }
  };
  const positionedNodes = pipeline?.nodes.map((node, idx) => ({
    ...node,
    position: getNodePosition(node.id, idx, node.position),
  })) || [];
  const viewport = computeFlowchartViewport(positionedNodes, canvasSize, zoomLevel);

  return (
    <div className="flex-1 flex flex-col h-full bg-[#0B0E14] text-[#E2E8F0] overflow-hidden select-none">
      {/* Top Flowchart Toolbar (Cognex Deep Steel Panel) */}
      <div className="h-12 bg-[#131822] border-b border-[#2B3547] px-5 flex items-center justify-between text-xs">
        <div className="flex items-center space-x-3">
          <div className="w-6 h-6 rounded bg-[#1A212E] border border-[#2B3547] flex items-center justify-center">
            <GitFork className="w-3.5 h-3.5 text-cyan-400" />
          </div>
          <div>
            <h2 className="font-bold text-xs text-[#F8FAFC] tracking-wide">
              {language === 'ko' ? '로컬 검사 플로우 (FLOWCHART)' : 'Local Inspection Flowchart'}
            </h2>
            <p className="text-[10px] font-mono text-[#94A3B8]">
              {language === 'ko'
                ? '원본 타일 분할 또는 검출 ROI 검사 · 로컬 결과 확인'
                : 'Original-resolution tiled segmentation or detector ROI inspection · local result'}
            </p>
          </div>
        </div>

        {/* Action Controls */}
        <div className="flex items-center space-x-2.5">
          {saveMessage && (
            <span className="text-[11px] font-mono text-emerald-400 font-bold">
              ✓ {saveMessage}
            </span>
          )}

          {/* Tab Selector */}
          <div className="flex bg-[#0B0E14] p-0.5 rounded border border-[#2B3547]">
            <button
              onClick={() => setActiveTab('flow')}
              className={`px-3 py-1 rounded text-xs font-bold transition-colors cursor-pointer ${
                activeTab === 'flow'
                  ? 'bg-[#1A212E] text-cyan-400 border border-[#2B3547]'
                  : 'text-[#94A3B8] hover:text-[#F8FAFC]'
              }`}
            >
              {language === 'ko' ? '파이프라인 회로망' : 'Circuit Topology'}
            </button>
            <button
              onClick={() => setActiveTab('results')}
              className={`px-3 py-1 rounded text-xs font-bold transition-colors cursor-pointer flex items-center space-x-1.5 ${
                activeTab === 'results'
                  ? 'bg-[#1A212E] text-cyan-400 border border-[#2B3547]'
                  : 'text-[#94A3B8] hover:text-[#F8FAFC]'
              }`}
            >
              <span>{language === 'ko' ? `검사 결과${executionResult ? ` (${executionResult.roi_count} ROI)` : ''}` : 'Inspection Results'}</span>
              {executionResult && (
                <div
                  className={`w-2 h-2 rounded-full ${
                    executionResult.final_verdict === 'REVIEW' ? 'bg-amber-400' :
                      executionResult.is_ok ? 'bg-emerald-400' : 'bg-rose-500'
                  }`}
                />
              )}
            </button>
          </div>

          {/* Save Pipeline Button */}
          <button
            onClick={() => savePipeline()}
            className="flex items-center space-x-1.5 px-3 py-1.5 bg-[#1A212E] hover:bg-[#222B3D] text-[#E2E8F0] rounded border border-[#2B3547] text-xs font-bold cursor-pointer transition-colors"
          >
            <Save className="w-3.5 h-3.5 text-[#94A3B8]" />
            <span>{language === 'ko' ? '회로 저장' : 'Save'}</span>
          </button>

          {/* Industrial Solid Run Button (Zero Gradients / Zero Diffuse Shadows) */}
          <button
            onClick={handleRun}
            disabled={isRunning || isLoading || needsModel}
            title={needsModel ? '검사 노드에 학습 모델 작업 ID를 지정하세요.' : undefined}
            className="flex items-center space-x-2 px-4 py-1.5 bg-[#10B981] hover:bg-[#059669] active:bg-[#047857] text-[#0B0E14] font-black rounded border border-[#34D399] text-xs transition-colors cursor-pointer disabled:opacity-50"
          >
            <Play className={`w-3.5 h-3.5 fill-current ${isRunning ? 'animate-spin' : ''}`} />
            <span>
              {isRunning
                ? language === 'ko'
                  ? '검사 실행 중...'
                  : 'Inspecting...'
                : language === 'ko'
                ? '회로 실행 (RUN)'
                : 'Run Circuit'}
            </span>
          </button>
        </div>
      </div>

      <div className="min-h-11 bg-[#111923] border-b border-[#2B3547] px-5 py-1.5 flex items-center justify-between gap-3 text-xs">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-bold text-slate-200 truncate">{pipeline?.name || '플로우 불러오는 중'}</span>
          {pipelineDirty && <span className="text-amber-400 whitespace-nowrap">미저장</span>}
          {pipeline?.nodes.some((node) => node.data.node_type === 'inspection' && node.data.task === 'segmentation') && (
            <span className="text-slate-400 truncate">
              분할 모델: {pipeline.nodes.find((node) => node.data.node_type === 'inspection')?.data.model_job_id || '검사 노드에서 지정 필요'}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={handleSingleModel}
            disabled={isLoading || isRunning}
            className="px-2.5 py-1 bg-cyan-950 hover:bg-cyan-900 text-cyan-200 border border-cyan-700 rounded font-bold disabled:opacity-50"
          >
            원본 타일 분할 플로우
          </button>
          <button
            onClick={handleRestoreSaved}
            disabled={isLoading || isRunning}
            className="px-2.5 py-1 bg-[#1A212E] hover:bg-[#222B3D] text-slate-300 border border-[#2B3547] rounded disabled:opacity-50"
          >
            저장본 불러오기
          </button>
        </div>
      </div>

      {/* Target Image Selector Bar */}
      <div className="h-10 bg-[#0E131C] border-b border-[#2B3547] px-5 flex items-center justify-between text-xs font-mono">
        <div className="flex items-center space-x-3">
          <span className="text-[#94A3B8] font-bold">INSPECTION TARGET:</span>
          {selectedImage ? (
            <div className="flex items-center space-x-2 bg-[#131822] px-2 py-0.5 rounded border border-[#2B3547]">
              {selectedImage.thumbnailUrl && (
                <img
                  src={resolveApiUrl(selectedImage.thumbnailUrl)}
                  alt="Thumbnail"
                  className="w-4 h-4 object-cover rounded bg-[#0B0E14]"
                />
              )}
              <span className="text-[#F8FAFC] font-bold truncate max-w-xs">{selectedImage.fileName}</span>
              <span className="text-[9px] text-cyan-400 bg-[#0B0E14] px-1.5 py-0.5 rounded border border-[#2B3547]">
                {selectedImage.source.toUpperCase()}
              </span>
            </div>
          ) : (
            <span className="text-[#94A3B8] italic">검사 이미지가 선택되지 않았습니다.</span>
          )}
        </div>

        <div className="flex items-center space-x-3">
          {/* Zoom Controls */}
          <div className="flex items-center space-x-1 bg-[#131822] px-1 py-0.5 rounded border border-[#2B3547]">
            <button
              onClick={() => setZoomLevel((z) => Math.max(0.6, z - 0.1))}
              className="p-1 hover:bg-[#1A212E] rounded text-[#94A3B8] hover:text-[#F8FAFC]"
              title="Zoom Out"
            >
              <ZoomOut className="w-3 h-3" />
            </button>
            <span className="text-[10px] font-mono tabular-nums px-1 text-slate-300">
              {Math.round(viewport.scale * 100)}%
            </span>
            <button
              onClick={() => setZoomLevel((z) => Math.min(1.5, z + 0.1))}
              className="p-1 hover:bg-[#1A212E] rounded text-[#94A3B8] hover:text-[#F8FAFC]"
              title="Zoom In"
            >
              <ZoomIn className="w-3 h-3" />
            </button>
            <button
              onClick={() => setZoomLevel(1.0)}
              className="p-1 hover:bg-[#1A212E] rounded text-[#94A3B8] hover:text-[#F8FAFC]"
              title="Fit graph"
            >
              <RotateCcw className="w-3 h-3" />
            </button>
          </div>

          <button
            onClick={() => setImagePickerOpen(true)}
            className="flex items-center space-x-1.5 px-2.5 py-1 bg-[#131822] hover:bg-[#1A212E] text-cyan-400 rounded border border-[#2B3547] font-bold cursor-pointer transition-colors"
          >
            <ImageIcon className="w-3 h-3" />
            <span>이미지 변경...</span>
          </button>
        </div>
      </div>

      {/* Error Notice */}
      {errorMessage && (
        <div className="bg-rose-950/90 border-b border-rose-800 px-5 py-2 flex items-center justify-between text-xs text-rose-300 font-mono">
          <div className="flex items-center space-x-2">
            <AlertTriangle className="w-4 h-4 text-rose-400" />
            <span>{errorMessage}</span>
          </div>
          <button onClick={clearError} className="text-rose-400 hover:text-rose-200 font-bold cursor-pointer">
            닫기
          </button>
        </div>
      )}

      {/* Main Flow Canvas or Results View */}
      {activeTab === 'flow' ? (
        <div className="flex-1 flex overflow-hidden">
          {/* 2D PCB DAG Circuit Canvas */}
          <div
            ref={canvasRef}
            className="flex-1 min-w-0 bg-[#0B0E14] overflow-auto relative"
            style={{
              backgroundImage:
                'linear-gradient(to right, #131822 1px, transparent 1px), linear-gradient(to bottom, #131822 1px, transparent 1px)',
              backgroundSize: '32px 32px',
            }}
          >
            <div
              style={{
                width: viewport.contentWidth,
                height: viewport.contentHeight,
                position: 'relative',
              }}
            >
              <div
                style={{
                  transform: `translate(${viewport.offsetX}px, ${viewport.offsetY}px) scale(${viewport.scale})`,
                  transformOrigin: '0 0',
                  width: viewport.layerWidth,
                  height: viewport.layerHeight,
                  position: 'absolute',
                  left: 0,
                  top: 0,
                }}
              >
              {/* SVG PCB Trace Wiring Overlay */}
              {pipeline && (
                <DAGCircuitOverlay
                  nodes={positionedNodes}
                  edges={pipeline.edges}
                  activeRunningNodeId={activeRunningNodeId}
                  finalVerdict={executionResult?.final_verdict}
                />
              )}

              {/* 2D Positioned Custom Nodes */}
              {positionedNodes.map((node) => {
                const isSelected = selectedNodeId === node.id;
                const isActive = activeRunningNodeId === node.id;
                const step = executionResult?.execution_steps?.find((s) => s.node_id === node.id);
                const isStepPassed = step?.status === 'passed';
                const isFlaggedNg = step && step.status === 'flagged_ng';
                const pos = node.position;

                return (
                  <div
                    key={node.id}
                    style={{
                      position: 'absolute',
                      left: pos.x,
                      top: pos.y,
                    }}
                  >
                    <CustomNode
                      node={{ ...node, position: pos }}
                      isSelected={isSelected}
                      isActive={isActive}
                      isPassed={!!isStepPassed}
                      isFlaggedNg={!!isFlaggedNg}
                      isSkipped={step?.status === 'skipped'}
                      isReviewRequired={step?.status === 'review_required'}
                      latencyMs={step?.latency_ms}
                      onSelect={() => selectNode(node.id)}
                    />
                  </div>
                );
              })}
              </div>
            </div>
          </div>

          {/* Node Property Inspector Sidebar (Cognex Deep Steel Panel) */}
          <div className="w-80 shrink-0 bg-[#131822] border-l border-[#2B3547] p-4 flex flex-col space-y-4">
            <h3 className="text-xs font-bold text-[#F8FAFC] uppercase tracking-wider flex items-center space-x-2 border-b border-[#2B3547] pb-2">
              <Sliders className="w-3.5 h-3.5 text-cyan-400" />
              <span>{language === 'ko' ? '노드 속성 (INSPECTOR)' : 'Node Properties'}</span>
            </h3>

            {selectedNode ? (
              <div className="space-y-4 text-xs font-mono">
                <div>
                  <label className="text-[#94A3B8] block mb-1">노드 명칭 (LABEL)</label>
                  <input
                    type="text"
                    value={selectedNode.data.label}
                    onChange={(e) => updateNodeData(selectedNode.id, { label: e.target.value })}
                    className="w-full bg-[#1A212E] border border-[#2B3547] rounded px-2.5 py-1.5 text-[#F8FAFC] focus:border-cyan-400 outline-none"
                  />
                </div>

                {(selectedNode.data.node_type === 'detection_crop' || selectedNode.data.node_type === 'inspection') && (
                  <div className="space-y-2">
                    <label className="text-[#94A3B8] block">학습 모델 작업 ID (job_...)</label>
                    <input
                      type="text"
                      value={selectedNode.data.model_job_id || ''}
                      onChange={(e) => updateNodeData(selectedNode.id, { model_job_id: e.target.value })}
                      placeholder="job_1234567890_abcdef"
                      className="w-full bg-[#1A212E] border border-[#2B3547] rounded px-2.5 py-1.5 text-[#F8FAFC] focus:border-cyan-400 outline-none"
                    />
                    {selectedNode.data.node_type === 'inspection' && (
                      <select
                        value={selectedNode.data.task || 'anomaly'}
                        onChange={(e) => updateNodeData(selectedNode.id, { task: e.target.value })}
                        className="w-full bg-[#1A212E] border border-[#2B3547] rounded px-2.5 py-1.5 text-[#F8FAFC]"
                      >
                        <option value="anomaly">Anomaly</option>
                        <option value="segmentation">Segmentation</option>
                        <option value="classification">Classification</option>
                      </select>
                    )}
                    <p className="text-amber-400 text-[10px]">
                      {selectedNode.data.node_type === 'inspection' && !pipeline?.nodes.some((node) => node.data.node_type === 'detection_crop')
                        ? '원본 해상도를 타일로 검사합니다. 타일 상한 초과 시 REVIEW로 표시하고, 결과 이미지는 축소 미리보기입니다.'
                        : '검출 모델과 검사 모델이 모두 필요합니다. 결과는 로컬 화면에만 표시됩니다.'}
                    </p>
                  </div>
                )}

                {selectedNode.data.threshold !== undefined &&
                  (selectedNode.data.node_type === 'inspection' || selectedNode.data.node_type === 'detection_crop' ||
                    (selectedNode.data.node_type === 'decision' && selectedNode.data.rule === 'score_gt_threshold')) && (
                  <div>
                    <div className="flex justify-between text-[#94A3B8] mb-1">
                      <span>결함 판정 임계치 (THRESHOLD)</span>
                      <span className="text-cyan-400 font-bold tabular-nums">
                        {selectedNode.data.threshold.toFixed(2)}
                      </span>
                    </div>
                    <input
                      type="range"
                      min="0.05"
                      max="0.95"
                      step="0.01"
                      value={selectedNode.data.threshold}
                      onChange={(e) =>
                        updateNodeData(selectedNode.id, { threshold: parseFloat(e.target.value) })
                      }
                      className="w-full accent-cyan-400 h-1 bg-[#1A212E] rounded cursor-pointer"
                    />
                  </div>
                )}

                {selectedNode.data.node_type === 'inspection' && selectedNode.data.task === 'segmentation' && (
                  <div>
                    <label className="text-[#94A3B8] block mb-1">
                      최소 결함 면적 ({pipeline?.nodes.some((node) => node.data.node_type === 'detection_crop')
                        ? '모델 입력 픽셀' : '검사 이미지 픽셀'})
                    </label>
                    <input
                      type="number"
                      min="1"
                      value={selectedNode.data.params?.min_defect_area_px ?? 8}
                      onChange={(e) => updateNodeData(selectedNode.id, {
                        params: { ...selectedNode.data.params, min_defect_area_px: Math.max(1, Number(e.target.value) || 1) },
                      })}
                      className="w-full bg-[#1A212E] border border-[#2B3547] rounded px-2.5 py-1.5 text-[#F8FAFC] tabular-nums focus:border-cyan-400 outline-none"
                    />
                  </div>
                )}

                {selectedNode.data.node_type === 'detection_crop' && selectedNode.data.crop_padding !== undefined && (
                  <div>
                    <label className="text-[#94A3B8] block mb-1">ROI 확장 패딩 (PX)</label>
                    <input
                      type="number"
                      value={selectedNode.data.crop_padding}
                      onChange={(e) =>
                        updateNodeData(selectedNode.id, {
                          crop_padding: parseInt(e.target.value) || 0,
                        })
                      }
                      className="w-full bg-[#1A212E] border border-[#2B3547] rounded px-2.5 py-1.5 text-[#F8FAFC] tabular-nums focus:border-cyan-400 outline-none"
                    />
                  </div>
                )}

                {selectedNode.data.node_type === 'decision' && selectedNode.data.rule && (
                  <div>
                    <label className="text-[#94A3B8] block mb-1">판정 룰 정책 (RULE POLICY)</label>
                    <div className="bg-[#1A212E] p-2 rounded border border-[#2B3547] text-amber-400 font-bold">
                      {selectedNode.data.rule}
                    </div>
                  </div>
                )}

                <div className="pt-4 border-t border-[#2B3547] text-[10px] text-[#94A3B8] space-y-1">
                  <div>NODE ID: <span className="text-slate-300 font-bold">{selectedNode.id}</span></div>
                  <div>TYPE: <span className="text-slate-300 font-bold">{selectedNode.data.node_type}</span></div>
                  <div>COORD: <span className="text-slate-300 tabular-nums">({selectedNode.position?.x ?? 0}, {selectedNode.position?.y ?? 0})</span></div>
                </div>
              </div>
            ) : (
              <div className="text-[#94A3B8] text-xs font-mono italic text-center py-12">
                회로망에서 설정할 검사 노드를 선택하세요.
              </div>
            )}
          </div>
        </div>
      ) : (
        <IntermediateCropDrawer />
      )}

      {/* Modals */}
      <ImagePickerModal isOpen={isImagePickerOpen} onClose={() => setImagePickerOpen(false)} />
      <CropDetailModal crop={inspectedCrop} onClose={() => setInspectedCrop(null)} />
    </div>
  );
};

export default FlowchartStudio;
