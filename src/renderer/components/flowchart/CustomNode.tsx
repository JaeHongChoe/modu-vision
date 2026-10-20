/**
 * src/renderer/components/flowchart/CustomNode.tsx
 * Industrial DAG Circuit Node.
 * Features:
 *  - Dark steel chassis theme (#1A212E card, #131822 header, 1px #2B3547 hairline border).
 *  - Circular LED annunciators (Amber Standby, Pulsing Cyan Active, Emerald Pass, Crimson Fail)
 *    housed in a machined metallic bezel ring.
 *  - Discrete tabular-nums font-mono latency badge.
 *  - Discrete physical input/output connection terminal blocks with color-coded connector pins.
 *  - Zero optical blurs, zero color gradients, zero diffuse glowing shadows.
 */

import React from 'react';
import {
  AlertTriangle,
  Camera,
  Crop,
  Cpu,
  Layers,
  Microscope,
  Scan,
  ShieldAlert,
} from 'lucide-react';
import type { FlowNode } from '../../types';
import { FLOW_NODE_WIDTH } from './flowchartViewport';
import { flowNodePorts, type FlowPortPayload } from './flowchartGraph';

interface CustomNodeProps {
  node: FlowNode;
  isSelected: boolean;
  isActive: boolean;
  isPassed: boolean;
  isFlaggedNg: boolean;
  isSkipped?: boolean;
  isReviewRequired?: boolean;
  latencyMs?: number;
  isDetectorOnly?: boolean;
  onSelect: () => void;
  onRemove?: () => void;
  editingDisabled?: boolean;
  /** Called with the payloads of the output port the connection starts from. */
  onConnectStart?: (payloads: FlowPortPayload[]) => void;
  onConnectFinish?: () => void;
  isConnectionSource?: boolean;
  /** This node's own validation problems, shown on the node (S2-05). */
  issues?: string[];
  /** Screen language of the visible type and port words; button names stay English for automation. */
  language?: 'ko' | 'en';
}

const NODE_TYPE_KO: Record<string, string> = {
  input: '입력', inspection: '검사 모델', decision: '판정', output: '출력', fixed_roi: '고정 ROI',
  blob_measure: '블롭 측정', patch_split: '패치 분할', preprocess: '전처리', measurement: '측정', aggregate: '집계',
};
const PAYLOAD_KO: Record<string, string> = { IMAGE: '이미지', ROI: 'ROI', RESULT: '결과', VERDICT: '판정' };

/** The visible Korean words for a port label such as "IMAGE/ROI IN" or "DEFECT BOXES": what the port carries. The side
 * of the node already says the direction (inputs left, outputs right) and the tooltip and button name repeat it, so the
 * words stay short enough for both sides of a fixed-width node on a large monitor. Unknown labels stay as they are. */
const TASK_KO: Record<string, string> = {classification: '분류', detection: '객체 검출', segmentation: '영역 분할', anomaly: '이상 탐지'};
const RULE_KO: Record<string, string> = {any_ng: '하나라도 NG', all_ng: '모두 NG', any_defect_is_ng: '결함 하나라도 NG', score_gt_threshold: '최고 점수 임계치',
  max_flaws_allowed: '허용 NG 검사 영역 수', aggregate_verdict: '집계 결과 판정', patch_recipe: '저장된 패치 판정 설정'};

export function koreanPortLabel(label: string): string {
  if (label === 'DEFECT BOXES') return '결함 박스';
  const match = /^(.+) (IN|OUT)$/.exec(label);
  if (!match) return label;
  return match[1].split('/').map((part) => PAYLOAD_KO[part] ?? part).join('·');
}

export const CustomNode: React.FC<CustomNodeProps> = ({
  node,
  isSelected,
  isActive,
  isPassed,
  isFlaggedNg,
  isSkipped,
  isReviewRequired,
  latencyMs,
  isDetectorOnly = false,
  onSelect,
  onRemove,
  editingDisabled = false,
  onConnectStart,
  onConnectFinish,
  isConnectionSource = false,
  language = 'ko',
  issues = [],
}) => {
  const nodeType = node.data.node_type;
  const nodeLabel = node.data.label || node.id;

  // Port colour by the payload it carries (several payloads: the first one's colour).
  const getPortColor = (payloads: FlowPortPayload[]): string => {
    switch (payloads[0]) {
      case 'image':
        return '#06B6D4'; // Cyan
      case 'roi':
        return '#A855F7'; // Purple
      case 'result':
        return '#3B82F6'; // Cobalt Blue
      case 'verdict':
        return '#F59E0B'; // Amber
      default:
        return '#94A3B8';
    }
  };
  // Messages name the node they belong to; on the node itself the name is redundant.
  // (an unnamed or emptied node's messages carry the same prefix the validator wrote, so it is stripped as written)
  const prefix = `${node.data.label}: `;
  const ownIssues = issues.map((message) => message.startsWith(prefix) ? message.slice(prefix.length) : message);

  // Node Icon
  const getNodeIcon = () => {
    switch (nodeType) {
      case 'input':
        return <Camera className="w-3.5 h-3.5 text-cyan-400" />;
      case 'detection_crop':
        return <Scan className="w-3.5 h-3.5 text-amber-400" />;
      case 'patch_split':
      case 'preprocess':
      case 'fixed_roi':
        return <Crop className="w-3.5 h-3.5 text-sky-400" />;
      case 'inspection':
        return <Microscope className="w-3.5 h-3.5 text-purple-400" />;
      case 'measurement':
      case 'blob_measure':
        return <Scan className="w-3.5 h-3.5 text-teal-400" />;
      case 'aggregate':
        return <Layers className="w-3.5 h-3.5 text-indigo-400" />;
      case 'decision':
        return <ShieldAlert className="w-3.5 h-3.5 text-rose-400" />;
      case 'output':
        return node.id.includes('ng') || node.id.includes('reject') ? (
          <AlertTriangle className="w-3.5 h-3.5 text-rose-400" />
        ) : (
          <Cpu className="w-3.5 h-3.5 text-emerald-400" />
        );
      default:
        return <Layers className="w-3.5 h-3.5 text-slate-400" />;
    }
  };

  // Typed ports from the payload rules connections are checked with (S2-05); the free-form ports older saved flows
  // carry in node.data.ports are not shown, since they did not say what a connection may carry.
  const { inputs, outputs } = flowNodePorts(node);
  // A detection model feeding the decision directly turns its boxes into defects; other models keep 'RESULT OUT'.
  const shownPort = (label: string) => language === 'ko' ? koreanPortLabel(label) : label;
  // The card's spec rows use the properties panel's Korean words on a Korean screen; English keeps the old labels.
  const ko = language === 'ko';
  const specLabel = (en: string, kr: string) => `${ko ? kr : en}:`;
  const taskName = (task: string) => ko ? (TASK_KO[task] || task) : task.toUpperCase();
  const ruleName = (rule: string) => ko ? (RULE_KO[rule] || rule) : rule;
  const outputLabel = (label: string) => nodeType === 'detection_crop' && isDetectorOnly && label === 'RESULT OUT' ? 'DEFECT BOXES' : label;
  // LED Annunciator Optical State
  const getAnnunciatorState = () => {
    if (isActive) return { color: '#06B6D4', pulse: true, label: 'BUSY' };
    if (isFlaggedNg) return { color: '#EF4444', pulse: false, label: 'FAIL' };
    if (isReviewRequired) return { color: '#F59E0B', pulse: false, label: 'REVIEW' };
    if (isSkipped) return { color: '#F59E0B', pulse: false, label: 'SKIP' };
    if (isPassed) return { color: '#10B981', pulse: false, label: 'PASS' };
    return { color: '#F59E0B', pulse: false, label: 'STBY' };
  };

  const led = getAnnunciatorState();

  return (
    <div
      role="group"
      aria-roledescription="플로우 노드"
      aria-label={`${nodeLabel} 노드 · ${isSelected ? '선택됨' : '선택 안됨'}`}
      tabIndex={0}
      data-flow-node-focus
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget || event.altKey || event.ctrlKey || event.metaKey) return;
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault(); onSelect();
        } else if (event.key === 'Delete' && !editingDisabled && onRemove) {
          event.preventDefault(); onRemove();
        }
      }}
      onClick={onSelect}
      style={{
        width: FLOW_NODE_WIDTH,
      }}
      className={`focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-cyan-300 relative select-none rounded-[4px] border transition-colors cursor-pointer bg-[#1A212E] ${
        isConnectionSource
          ? 'border-cyan-300 ring-2 ring-cyan-400 z-20'
          : isSelected
          ? 'border-cyan-400 ring-1 ring-cyan-400 z-20'
          : isFlaggedNg
          ? 'border-rose-500 hover:border-rose-400 z-10'
          : isPassed
          ? 'border-emerald-600/80 hover:border-emerald-500'
          : ownIssues.length
          ? 'border-amber-500 hover:border-amber-400'
          : 'border-[#2B3547] hover:border-[#475569]'
      }`}
    >
      {/* Validation problems of this node: positioned over the frame so the port layout does not move */}
      {ownIssues.length > 0 && (
        <div role="note" aria-label={`${node.data.label} 문제 ${ownIssues.length}건: ${ownIssues.join(' / ')}`} title={ownIssues.join('\n')}
          className="absolute -top-2.5 right-2 z-30 rounded border border-amber-500 bg-[#2A1F0A] px-1.5 py-0.5 text-[10px] font-semibold text-amber-200">
          ⚠ {ownIssues.length}
        </div>
      )}
      {/* Precision 1px Chassis Top Bevel */}
      <div className="h-[2px] w-full bg-[#2B3547] rounded-t-[3px]" />

      {/* Node Header Bar */}
      <div className="h-8 bg-[#131822] border-b border-[#2B3547] px-3 flex items-center justify-between">
        <div className="flex items-center space-x-2">
          {getNodeIcon()}
          <span className="text-[11px] font-mono uppercase tracking-wide text-slate-200 font-bold truncate max-w-[120px]">
            {language === 'ko'
              ? (nodeType === 'detection_crop' ? (isDetectorOnly ? '결함 검출' : 'ROI 검출') : NODE_TYPE_KO[nodeType] ?? nodeType)
              : nodeType === 'detection_crop' ? (isDetectorOnly ? 'DEFECT DETECTION' : 'ROI DETECTION')
                : nodeType === 'fixed_roi' ? 'FIXED ROI'
                  : nodeType === 'blob_measure' ? 'BLOB MEASURE' : nodeType}
          </span>
        </div>

        {/* Metallic Circular LED Annunciator + Latency Badge */}
        <div className="flex items-center space-x-2">
          {latencyMs !== undefined && (
            <div className="bg-[#0B0E14] px-1.5 py-0.5 rounded border border-[#2B3547] flex items-center space-x-1">
              <span className="text-[10px] font-mono tabular-nums font-bold text-[#F8FAFC]">
                {latencyMs.toFixed(1)}
              </span>
              <span className="text-[9px] font-mono text-[#64748B]">ms</span>
            </div>
          )}

          {/* Machined Metallic Bezel Housing */}
          <div
            className="w-4 h-4 rounded-full bg-[#1E293B] border border-[#475569] flex items-center justify-center p-0.5"
            title={`Status: ${led.label}`}
          >
            {/* Recessed Matte Black Cavity */}
            <div className="w-2.5 h-2.5 rounded-full bg-[#0B0E14] flex items-center justify-center">
              {/* Core LED Disc */}
              <div
                style={{ backgroundColor: led.color }}
                className={`w-2 h-2 rounded-full ${led.pulse ? 'animate-pulse' : ''}`}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Node Label & Primary Specs */}
      <div className="p-3 bg-[#1A212E]">
        <h4 className="text-[13px] font-bold text-[#F8FAFC] truncate mb-2" title={node.data.label}>{node.data.label}</h4>

        {/* Specs Table */}
        <div className="space-y-1 text-[11px] font-mono bg-[#131822] p-2 rounded border border-[#2B3547]">
          {(nodeType === 'inspection' || nodeType === 'detection_crop' || nodeType === 'preprocess') && node.data.task && (
            <div className="flex justify-between items-center">
              <span className="text-slate-400">{specLabel('TASK', '종류')}</span>
              <span className="text-slate-300 font-semibold">{taskName(node.data.task)}</span>
            </div>
          )}
          {(nodeType === 'inspection' || nodeType === 'detection_crop' ||
            (nodeType === 'decision' && node.data.rule === 'score_gt_threshold')) && node.data.threshold !== undefined && (
            <div className="flex justify-between items-center">
              <span className="text-slate-400">{specLabel('THRESHOLD', '기준 점수')}</span>
              <span className="text-cyan-400 font-bold tabular-nums">
                {ko ? node.data.threshold.toFixed(2) : `τ = ${node.data.threshold.toFixed(2)}`}
              </span>
            </div>
          )}
          {nodeType === 'detection_crop' && node.data.crop_padding !== undefined && (
            <div className="flex justify-between items-center">
              <span className="text-slate-400">{specLabel('ROI PADDING', 'ROI 여백')}</span>
              <span className="text-slate-300 tabular-nums">{node.data.crop_padding} px</span>
            </div>
          )}
          {nodeType === 'fixed_roi' && Array.isArray(node.data.params?.roi_bbox) && (
            <div className="space-y-0.5">
              <span className="text-slate-400">{specLabel('SOURCE PIXELS', '원본 픽셀')}</span>
              <div className="text-sky-300 tabular-nums">
                {node.data.params?.roi_bbox?.[0]}, {node.data.params?.roi_bbox?.[1]} → {node.data.params?.roi_bbox?.[2]}, {node.data.params?.roi_bbox?.[3]}
              </div>
            </div>
          )}
          {nodeType === 'measurement' && <div className="space-y-1 text-teal-200"><div>{ko ? '원본 길이·면적' : 'SOURCE LENGTH / AREA'}</div><div>{node.data.params?.paths?.length || 0} 경로 · {(node.data.params?.threshold_unit ?? (node.data.params?.calibration || node.data.params?.calibration_ref ? 'mm' : 'px'))==='mm'?'mm / mm²':'px / px²'}</div></div>}
          {nodeType === 'preprocess' && <div className="text-sky-200">{({learned_rotation:'학습 회전 보정',fitted_roi:'원본 회전 ROI 맞춤',enhancement:'학습 영상 개선',rotate:'회전',align:'방향 정렬',improve:'영상 개선'} as Record<string,string>)[node.data.params?.operation||'rotate']}</div>}
          {nodeType === 'blob_measure' && <>
            <div className="flex justify-between items-center">
              <span className="text-slate-400">{specLabel('MIN AREA', '최소 면적')}</span>
              <span className="text-teal-300 tabular-nums">{node.data.params?.min_blob_area_px ?? 1} px</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-slate-400">{specLabel('NG COUNT', 'NG 개수')}</span>
              <span className="text-teal-300 tabular-nums">{node.data.params?.min_blob_count_for_ng ?? 1}</span>
            </div>
          </>}
          {nodeType === 'aggregate' && node.data.rule && (
            <div className="flex justify-between items-center">
              <span className="text-slate-400">{specLabel('RULE', '규칙')}</span>
              <span className="text-indigo-300 font-semibold">{ko ? ruleName(node.data.rule) : node.data.rule.replace('_', ' ').toUpperCase()}</span>
            </div>
          )}
          {nodeType === 'decision' && node.data.rule && (
            <div className="flex justify-between items-center">
              <span className="text-slate-400">{specLabel('RULE', '규칙')}</span>
              <span className="text-amber-400 font-semibold">{ruleName(node.data.rule)}</span>
            </div>
          )}
        </div>
      </div>

      {/* Discrete Terminal Port Flanges (Left Inputs, Right Outputs) */}
      <div className="px-2 pb-2.5 pt-1 border-t border-[#2B3547] bg-[#151C28] flex justify-between items-start text-[9px] font-mono">
        {/* Left Inputs */}
        <div className="space-y-1.5 flex-1 pr-2">
          {inputs.map((port, index) => (
            <div key={port.id} className="flex items-center space-x-1.5">
              <button
                type="button"
                disabled={editingDisabled}
                data-flow-port={`${node.id}:in:${index}`}
                aria-label={`Connect to ${nodeLabel} ${port.label}`}
                title={`이 노드의 입력에 연결 (${shownPort(port.label)})`}
                onClick={(event) => { event.stopPropagation(); onConnectFinish?.(); }}
                style={{ borderColor: getPortColor(port.payloads) }}
                className="w-3.5 h-3.5 rounded-full bg-[#0B0E14] border-2 flex items-center justify-center shrink-0 hover:scale-125 focus:outline-cyan-400"
              >
                <div
                  style={{ backgroundColor: getPortColor(port.payloads) }}
                  className="w-1 h-1 rounded-full"
                />
              </button>
              <span className="text-slate-300 font-bold uppercase truncate">{shownPort(port.label)}</span>
            </div>
          ))}
        </div>

        {/* Right Outputs */}
        <div className="space-y-1.5 flex-1 pl-2 text-right">
          {outputs.map((port, index) => (
            <div key={port.id} className="flex items-center justify-end space-x-1.5">
              <span className="text-slate-300 font-bold uppercase truncate">{shownPort(outputLabel(port.label))}</span>
              <button
                type="button"
                disabled={editingDisabled}
                data-flow-port={`${node.id}:out:${index}`}
                aria-label={`Start connection from ${nodeLabel} ${outputLabel(port.label)}`}
                title={`여기서 연결 시작 (${shownPort(outputLabel(port.label))})`}
                onClick={(event) => { event.stopPropagation(); onConnectStart?.(port.payloads); }}
                style={{ borderColor: getPortColor(port.payloads) }}
                className="w-3.5 h-3.5 rounded-full bg-[#0B0E14] border-2 flex items-center justify-center shrink-0 hover:scale-125 focus:outline-cyan-400"
              >
                <div
                  style={{ backgroundColor: getPortColor(port.payloads) }}
                  className="w-1 h-1 rounded-full"
                />
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
export default CustomNode;
