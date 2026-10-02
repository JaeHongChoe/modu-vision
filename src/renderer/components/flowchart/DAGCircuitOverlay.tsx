/**
 * src/renderer/components/flowchart/DAGCircuitOverlay.tsx
 * Industrial PCB Trace Wire Router & Multi-Layer Circuit Overlay.
 * Features:
 *  - Orthogonal 45-degree chamfered copper trace generator.
 *  - Dual-layer trace rendering (Substrate Copper + Signal Carrier Trace).
 *  - Copper via test pads at connection endpoints and bend junctions.
 *  - Live execution signal packets animation.
 *  - Saved model fan-out and verdict output branches remain selectable.
 */

import React, { useLayoutEffect, useRef, useState } from 'react';
import type { FlowEdge, FlowNode, FlowchartExecutionStep } from '../../types';
import { FLOW_NODE_WIDTH } from './flowchartViewport';
import { flowEdgeKey, flowEdgeSourcePort, flowNodePorts } from './flowchartGraph';

interface DAGCircuitOverlayProps {
  nodes: FlowNode[];
  edges: FlowEdge[];
  activeRunningNodeId: string | null;
  finalVerdict?: 'OK' | 'NG' | 'REVIEW';
  routedOutputNodeId?: string;
  selectedEdgeId?: string | null;
  /** Each connection's own validation problems (S2-05). */
  edgeIssues?: Map<string, string[]>;
  executionSteps?: FlowchartExecutionStep[];
  onSelectEdge?: (edgeId: string) => void;
}

export const DAGCircuitOverlay: React.FC<DAGCircuitOverlayProps> = ({
  nodes,
  edges,
  activeRunningNodeId,
  finalVerdict,
  routedOutputNodeId,
  selectedEdgeId,
  edgeIssues,
  executionSteps,
  onSelectEdge,
}) => {
  // Map nodes by ID for O(1) coordinate lookup
  const nodeMap = new Map<string, FlowNode>();
  nodes.forEach((n) => nodeMap.set(n.id, n));

  // Node bodies differ in height (their spec rows), so the port pins are measured after each render, in this layer's
  // own coordinates (the canvas zoom is divided out); the estimate below is used only before the first measurement.
  const svgRef = useRef<SVGSVGElement>(null);
  const [pins, setPins] = useState<Record<string, { x: number; y: number }>>({});
  useLayoutEffect(() => {
    const svg = svgRef.current;
    const layer = svg?.parentElement;
    if (!svg || !layer || typeof svg.getBoundingClientRect !== 'function') return;
    const frame = svg.getBoundingClientRect();
    const scale = svg.clientWidth ? frame.width / svg.clientWidth : 1;
    const next: Record<string, { x: number; y: number }> = {};
    layer.querySelectorAll<HTMLElement>('[data-flow-port]').forEach((element) => {
      const box = element.getBoundingClientRect();
      next[element.dataset.flowPort as string] = {
        x: Math.round(((box.left + box.width / 2 - frame.left) / (scale || 1)) * 10) / 10,
        y: Math.round(((box.top + box.height / 2 - frame.top) / (scale || 1)) * 10) / 10,
      };
    });
    setPins((current) => JSON.stringify(current) === JSON.stringify(next) ? current : next);
  });
  const selectedByRun = new Set(executionSteps?.flatMap((step) => step.selected_edge_ids || []) || []);
  const hasExecutionTrace = Boolean(executionSteps?.some((step) => step.selected_edge_ids && step.selected_edge_ids.length > 0));

  // Calculates exact terminal pin coordinates
  const getPortCoord = (nodeId: string, direction: 'out' | 'in', portIndex = 0, totalPorts = 1) => {
    const measured = pins[`${nodeId}:${direction}:${portIndex}`];
    if (measured) return measured;
    const node = nodeMap.get(nodeId);
    if (!node) return { x: 0, y: 0 };

    const posX = node.position.x;
    const posY = node.position.y;
    const headerHeight = 34;
    const terminalArea = 32;

    const yOffset = headerHeight + 84 + ((portIndex + 0.5) / Math.max(1, totalPorts)) * terminalArea;

    return {
      x: direction === 'out' ? posX + FLOW_NODE_WIDTH : posX,
      y: posY + yOffset,
    };
  };

  // 45-degree chamfered PCB trace path generator
  const generatePcbPath = (x1: number, y1: number, x2: number, y2: number) => {
    const stub = 20;
    const dy = y2 - y1;
    const dx = x2 - x1;

    // Collinear direct trace
    if (Math.abs(dy) < 3) {
      return `M ${x1} ${y1} L ${x2} ${y2}`;
    }

    // 45-degree chamfered bend
    const chamfer = Math.min(Math.abs(dy), 16);
    const xMid = x1 + stub + (dx - 2 * stub - chamfer) / 2;

    return [
      `M ${x1} ${y1}`,
      `L ${x1 + stub} ${y1}`,
      `L ${xMid} ${y1}`,
      `L ${xMid + chamfer} ${y2}`,
      `L ${x2 - stub} ${y2}`,
      `L ${x2} ${y2}`,
    ].join(' ');
  };

  return (
    <svg ref={svgRef} className="absolute inset-0 w-full h-full pointer-events-none z-0">
      <defs>
        {/* Animated Signal Packet Pulse Pattern */}
        <style>{`
          @keyframes pcbSignalFlow {
            from { stroke-dashoffset: 24; }
            to { stroke-dashoffset: 0; }
          }
          .pcb-pulse-active {
            animation: pcbSignalFlow 0.8s linear infinite;
          }
        `}</style>
      </defs>

      {edges.map((edge, index) => {
        const sourceNode = nodeMap.get(edge.source);
        const targetNode = nodeMap.get(edge.target);
        if (!sourceNode || !targetNode) return null;

        // The typed port the edge's payload leaves from (a model's image/ROI or result output)
        const sourcePortCount = Math.max(1, flowNodePorts(sourceNode).outputs.length);
        const targetPortCount = Math.max(1, flowNodePorts(targetNode).inputs.length);

        const sourcePort = flowEdgeSourcePort(sourceNode, edge, targetNode);
        const p1 = getPortCoord(edge.source, 'out', sourcePort, sourcePortCount);
        const p2 = getPortCoord(edge.target, 'in', 0, targetPortCount);
        const issues = edgeIssues?.get(flowEdgeKey(edge, index)) || [];

        const pathD = generatePcbPath(p1.x, p1.y, p2.x, p2.y);
        const isActive = activeRunningNodeId === edge.source;
        const isVerdictBranch = sourceNode.data.node_type === 'decision';
        const wasRouted = isVerdictBranch && routedOutputNodeId === edge.target;

        // Trace Color Determination
        let traceColor = edge.isBranch === 'pass' ? '#16A34A' : edge.isBranch === 'fail' ? '#DC2626'
          : edge.isBranch === 'review' ? '#D97706' : '#475569';
        if (isActive) {
          traceColor = '#06B6D4'; // Electric Cyan Active
        } else if (hasExecutionTrace) {
          traceColor = selectedByRun.has(edge.id) ? '#38BDF8' : '#334155';
        } else if (finalVerdict && (!isVerdictBranch || wasRouted)) {
          traceColor = finalVerdict === 'OK' ? '#10B981' : finalVerdict === 'NG' ? '#EF4444' : '#F59E0B';
        }
        if (isVerdictBranch && wasRouted && finalVerdict) {
          traceColor = finalVerdict === 'OK' ? '#10B981' : finalVerdict === 'NG' ? '#EF4444' : '#F59E0B';
        }
        if (issues.length) traceColor = '#F59E0B';  // a connection with a problem is marked whatever its state

        const midX = (p1.x + p2.x) / 2;
        const midY = (p1.y + p2.y) / 2;

        return (
          <g key={flowEdgeKey(edge, index)} className="transition-all duration-300">
            {/* Layer 1: PCB Substrate Base Copper (Wide 5px) */}
            <path
              d={pathD}
              fill="none"
              stroke="#0E1624"
              strokeWidth="6"
              strokeLinecap="round"
            />
            <path
              d={pathD}
              fill="none"
              stroke="#1E293B"
              strokeWidth="4"
              strokeLinecap="round"
            />

            {/* Layer 2: Core Signal Trace (2px) */}
            <path
              d={pathD}
              data-flow-edge={edge.id}
              data-flow-from={`${edge.source}:out:${sourcePort}`}
              data-flow-to={`${edge.target}:in:0`}
              fill="none"
              stroke={traceColor}
              strokeWidth={selectedEdgeId === edge.id || issues.length ? 3 : 2}
              strokeDasharray={issues.length ? '2 3' : edge.isBranch && !isVerdictBranch ? '6 3' : undefined}
              strokeLinecap="round"
            />

            <path
              d={pathD}
              fill="none"
              stroke="transparent"
              strokeWidth="16"
              style={{ pointerEvents: 'stroke', cursor: 'pointer' }}
              role="button"
              tabIndex={0}
              aria-label={`Select connection ${edge.source} to ${edge.target}${edge.isBranch ? ` when ${edge.isBranch}` : ''}${issues.length ? `: ${issues.join(' / ')}` : ''}`}
              onClick={() => onSelectEdge?.(edge.id)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onSelectEdge?.(edge.id);
                }
              }}
            />

            {/* Layer 3: High-Frequency Electron Packet Pulse (Running State) */}
            {isActive && (
              <path
                d={pathD}
                fill="none"
                stroke="#F8FAFC"
                strokeWidth="2"
                strokeDasharray="4 12"
                className="pcb-pulse-active"
              />
            )}

            {/* Terminal Via Pads */}
            <circle cx={p1.x} cy={p1.y} r="3.5" fill="#0B0E14" stroke={traceColor} strokeWidth="1.5" />
            <circle cx={p1.x} cy={p1.y} r="1.5" fill="#F8FAFC" />

            <circle cx={p2.x} cy={p2.y} r="3.5" fill="#0B0E14" stroke={traceColor} strokeWidth="1.5" />
            <circle cx={p2.x} cy={p2.y} r="1.5" fill="#F8FAFC" />

            {/* Discrete Wire Label Badge */}
            {(edge.label || edge.isBranch || edge.predicate) && (
              <g transform={`translate(${midX}, ${midY - 10})`}>
                <rect
                  x="-60"
                  y="-8"
                  width="120"
                  height="16"
                  rx="3"
                  fill="#0B0E14"
                  stroke="#2B3547"
                  strokeWidth="1"
                />
                <text
                  x="0"
                  y="3.5"
                  textAnchor="middle"
                  fill="#94A3B8"
                  fontSize="8.5"
                  fontWeight="bold"
                  fontFamily="monospace"
                  letterSpacing="0.05em"
                >
                  {edge.predicate ? `${edge.predicate.class_name} ${edge.predicate.operator === 'present' ? '있음' : '없음'}` : edge.isBranch === 'pass' ? 'OK' : edge.isBranch === 'fail' ? 'NG' : edge.isBranch === 'review' ? 'REVIEW' : edge.label}
                </text>
              </g>
            )}
          </g>
        );
      })}
    </svg>
  );
};

export default DAGCircuitOverlay;
