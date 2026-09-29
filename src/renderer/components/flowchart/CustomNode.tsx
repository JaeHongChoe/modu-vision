/**
 * src/renderer/components/flowchart/CustomNode.tsx
 * Industrial DAG Circuit Node (Cognex VisionPro / Keyence XG-X Physical Standard).
 * Features:
 *  - Cognex Deep Steel chassis theme (#1A212E card, #131822 header, 1px #2B3547 hairline border).
 *  - Keyence physical-style circular LED annunciators (Amber Standby, Pulsing Cyan Active, Emerald Pass, Crimson Fail)
 *    housed in a machined metallic bezel ring.
 *  - Discrete tabular-nums font-mono latency badge.
 *  - Discrete physical input/output connection terminal blocks with color-coded connector pins.
 *  - Zero optical blurs, zero color gradients, zero diffuse glowing shadows.
 */

import React from 'react';
import {
  AlertTriangle,
  Camera,
  Cpu,
  Layers,
  Microscope,
  Scan,
  ShieldAlert,
} from 'lucide-react';
import type { FlowNode, NodePort, PortType } from '../../types';
import { FLOW_NODE_WIDTH } from './flowchartViewport';

interface CustomNodeProps {
  node: FlowNode;
  isSelected: boolean;
  isActive: boolean;
  isPassed: boolean;
  isFlaggedNg: boolean;
  isSkipped?: boolean;
  isReviewRequired?: boolean;
  latencyMs?: number;
  onSelect: () => void;
  onPortHover?: (port: NodePort, nodeId: string) => void;
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
  onSelect,
}) => {
  const nodeType = node.data.node_type;

  // Port Color Mapping
  const getPortColor = (type: PortType): string => {
    switch (type) {
      case 'image':
        return '#06B6D4'; // Cyan
      case 'trigger':
        return '#F59E0B'; // Amber
      case 'mask':
        return '#A855F7'; // Purple
      case 'data':
        return '#3B82F6'; // Cobalt Blue
      case 'pass':
        return '#10B981'; // Emerald
      case 'fail':
        return '#EF4444'; // Crimson
      case 'plc':
        return '#10B981';
      default:
        return '#94A3B8';
    }
  };

  // Node Icon
  const getNodeIcon = () => {
    switch (nodeType) {
      case 'input':
        return <Camera className="w-3.5 h-3.5 text-cyan-400" />;
      case 'detection_crop':
        return <Scan className="w-3.5 h-3.5 text-amber-400" />;
      case 'inspection':
        return <Microscope className="w-3.5 h-3.5 text-purple-400" />;
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

  // Default terminal ports fallback based on node_type if not explicitly defined in node.data
  const getDefaultInputs = (): NodePort[] => {
    switch (nodeType) {
      case 'input':
        return [];
      case 'detection_crop':
        return [
          { id: 'in_img', name: 'Image In', type: 'image', direction: 'in', label: 'IMG IN', pinNumber: 1 },
        ];
      case 'inspection':
        return [
          { id: 'in_img', name: 'Image In', type: 'image', direction: 'in', label: 'IMAGE IN', pinNumber: 1 },
        ];
      case 'decision':
        return [
          { id: 'in_data', name: 'Scores In', type: 'data', direction: 'in', label: 'SCORES IN', pinNumber: 1 },
        ];
      case 'output':
        return [{ id: 'in_result', name: 'Verdict In', type: 'data', direction: 'in', label: 'VERDICT IN', pinNumber: 1 }];
      default:
        return [{ id: 'in_def', name: 'Input', type: 'data', direction: 'in', label: 'IN 1', pinNumber: 1 }];
    }
  };

  const getDefaultOutputs = (): NodePort[] => {
    switch (nodeType) {
      case 'input':
        return [
          { id: 'out_img', name: 'Image Out', type: 'image', direction: 'out', label: 'IMG OUT', pinNumber: 1 },
        ];
      case 'detection_crop':
        return [
          { id: 'out_img', name: 'ROI Crops Out', type: 'image', direction: 'out', label: 'ROI CROPS', pinNumber: 1 },
        ];
      case 'inspection':
        return [
          { id: 'out_data', name: 'Defect Scores', type: 'data', direction: 'out', label: 'DEFECT DATA', pinNumber: 1 },
        ];
      case 'decision':
        return [
          { id: 'out_verdict', name: 'Verdict Out', type: 'data', direction: 'out', label: 'VERDICT', pinNumber: 1 },
        ];
      case 'output':
        return [{ id: 'out_result', name: 'Local Result', type: 'data', direction: 'out', label: 'LOCAL VIEW', pinNumber: 1 }];
      default:
        return [{ id: 'out_def', name: 'Output', type: 'data', direction: 'out', label: 'OUT 1', pinNumber: 1 }];
    }
  };

  const inputs: NodePort[] = node.data.ports?.inputs || getDefaultInputs();
  const outputs: NodePort[] = node.data.ports?.outputs || getDefaultOutputs();

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
      onClick={onSelect}
      style={{
        width: FLOW_NODE_WIDTH,
      }}
      className={`relative select-none rounded-[4px] border transition-colors cursor-pointer bg-[#1A212E] ${
        isSelected
          ? 'border-cyan-400 ring-1 ring-cyan-400 z-20'
          : isFlaggedNg
          ? 'border-rose-500 hover:border-rose-400 z-10'
          : isPassed
          ? 'border-emerald-600/80 hover:border-emerald-500'
          : 'border-[#2B3547] hover:border-[#475569]'
      }`}
    >
      {/* Precision 1px Chassis Top Bevel */}
      <div className="h-[2px] w-full bg-[#2B3547] rounded-t-[3px]" />

      {/* Node Header Bar */}
      <div className="h-8 bg-[#131822] border-b border-[#2B3547] px-3 flex items-center justify-between">
        <div className="flex items-center space-x-2">
          {getNodeIcon()}
          <span className="text-[10px] font-mono uppercase tracking-wider text-slate-300 font-bold truncate max-w-[110px]">
            {node.data.node_type}
          </span>
        </div>

        {/* Keyence Physical Metallic Circular LED Annunciator + Latency Badge */}
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
        <h4 className="text-xs font-bold text-[#F8FAFC] truncate mb-2">{node.data.label}</h4>

        {/* Specs Table */}
        <div className="space-y-1 text-[11px] font-mono bg-[#131822] p-2 rounded border border-[#2B3547]">
          {(nodeType === 'inspection' || nodeType === 'detection_crop') && node.data.task && (
            <div className="flex justify-between items-center">
              <span className="text-[#64748B]">TASK:</span>
              <span className="text-slate-300 font-semibold">{node.data.task.toUpperCase()}</span>
            </div>
          )}
          {(nodeType === 'inspection' || nodeType === 'detection_crop' ||
            (nodeType === 'decision' && node.data.rule === 'score_gt_threshold')) && node.data.threshold !== undefined && (
            <div className="flex justify-between items-center">
              <span className="text-[#64748B]">THRESHOLD:</span>
              <span className="text-cyan-400 font-bold tabular-nums">
                τ = {node.data.threshold.toFixed(2)}
              </span>
            </div>
          )}
          {nodeType === 'detection_crop' && node.data.crop_padding !== undefined && (
            <div className="flex justify-between items-center">
              <span className="text-[#64748B]">ROI PADDING:</span>
              <span className="text-slate-300 tabular-nums">{node.data.crop_padding} px</span>
            </div>
          )}
          {nodeType === 'decision' && node.data.rule && (
            <div className="flex justify-between items-center">
              <span className="text-[#64748B]">RULE:</span>
              <span className="text-amber-400 font-semibold">{node.data.rule}</span>
            </div>
          )}
        </div>
      </div>

      {/* Discrete Terminal Port Flanges (Left Inputs, Right Outputs) */}
      <div className="px-2 pb-2.5 pt-1 border-t border-[#2B3547] bg-[#151C28] flex justify-between items-start text-[9px] font-mono">
        {/* Left Inputs */}
        <div className="space-y-1.5 flex-1 pr-2">
          {inputs.map((port) => (
            <div key={port.id} className="flex items-center space-x-1.5">
              <div
                style={{ borderColor: getPortColor(port.type) }}
                className="w-2.5 h-2.5 rounded-full bg-[#0B0E14] border-2 flex items-center justify-center shrink-0"
              >
                <div
                  style={{ backgroundColor: getPortColor(port.type) }}
                  className="w-1 h-1 rounded-full"
                />
              </div>
              <span className="text-slate-300 font-bold uppercase truncate">{port.label}</span>
            </div>
          ))}
        </div>

        {/* Right Outputs */}
        <div className="space-y-1.5 flex-1 pl-2 text-right">
          {outputs.map((port) => (
            <div key={port.id} className="flex items-center justify-end space-x-1.5">
              <span className="text-slate-300 font-bold uppercase truncate">{port.label}</span>
              <div
                style={{ borderColor: getPortColor(port.type) }}
                className="w-2.5 h-2.5 rounded-full bg-[#0B0E14] border-2 flex items-center justify-center shrink-0"
              >
                <div
                  style={{ backgroundColor: getPortColor(port.type) }}
                  className="w-1 h-1 rounded-full"
                />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default CustomNode;
