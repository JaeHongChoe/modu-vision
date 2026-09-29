/**
 * src/renderer/components/flowchart/DAGCircuitOverlay.tsx
 * Industrial PCB Trace Wire Router & Multi-Layer Circuit Overlay.
 * Features:
 *  - Orthogonal 45-degree chamfered copper trace generator.
 *  - Dual-layer trace rendering (Substrate Copper + Signal Carrier Trace).
 *  - Copper via test pads at connection endpoints and bend junctions.
 *  - Live execution signal packets animation.
 *  - High-visibility visual branching for PASS Route (Emerald) vs NG Diverter (Crimson).
 */

import React from 'react';
import type { FlowEdge, FlowNode } from '../../types';

interface DAGCircuitOverlayProps {
  nodes: FlowNode[];
  edges: FlowEdge[];
  activeRunningNodeId: string | null;
  finalVerdict?: 'OK' | 'NG';
}

export const DAGCircuitOverlay: React.FC<DAGCircuitOverlayProps> = ({
  nodes,
  edges,
  activeRunningNodeId,
  finalVerdict,
}) => {
  const NODE_WIDTH = 272;

  // Map nodes by ID for O(1) coordinate lookup
  const nodeMap = new Map<string, FlowNode>();
  nodes.forEach((n) => nodeMap.set(n.id, n));

  // Calculates exact terminal pin coordinates
  const getPortCoord = (nodeId: string, direction: 'out' | 'in', portIndex = 0, totalPorts = 1) => {
    const node = nodeMap.get(nodeId);
    if (!node) return { x: 0, y: 0 };

    const posX = node.position.x;
    const posY = node.position.y;
    const headerHeight = 34;
    const terminalArea = 32;

    const yOffset = headerHeight + 84 + ((portIndex + 0.5) / Math.max(1, totalPorts)) * terminalArea;

    return {
      x: direction === 'out' ? posX + NODE_WIDTH : posX,
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
    <svg className="absolute inset-0 w-full h-full pointer-events-none z-0">
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

      {edges.map((edge) => {
        const sourceNode = nodeMap.get(edge.source);
        const targetNode = nodeMap.get(edge.target);
        if (!sourceNode || !targetNode) return null;

        const isBranchPass = edge.isBranch === 'pass' || edge.id.includes('pass') || edge.target.includes('pass');
        const isBranchFail = edge.isBranch === 'fail' || edge.id.includes('fail') || edge.target.includes('ng') || edge.target.includes('reject');

        // Calculate discrete pin index
        const sourcePortCount = sourceNode.data.ports?.outputs?.length || 1;
        const targetPortCount = targetNode.data.ports?.inputs?.length || 1;

        const portIndexOut = isBranchFail ? Math.min(1, sourcePortCount - 1) : 0;
        const portIndexIn = 0;

        const p1 = getPortCoord(edge.source, 'out', portIndexOut, sourcePortCount);
        const p2 = getPortCoord(edge.target, 'in', portIndexIn, targetPortCount);

        const pathD = generatePcbPath(p1.x, p1.y, p2.x, p2.y);
        const isActive = activeRunningNodeId === edge.source;

        // Trace Color Determination
        let traceColor = '#334155'; // Standby Dark Steel Copper
        if (isActive) {
          traceColor = '#06B6D4'; // Electric Cyan Active
        } else if (isBranchPass) {
          traceColor = finalVerdict === 'OK' ? '#10B981' : '#1E293B';
        } else if (isBranchFail) {
          traceColor = finalVerdict === 'NG' ? '#EF4444' : '#1E293B';
        } else if (finalVerdict) {
          traceColor = '#3B82F6'; // Completed Signal Bus
        }

        const midX = (p1.x + p2.x) / 2;
        const midY = (p1.y + p2.y) / 2;

        return (
          <g key={edge.id} className="transition-all duration-300">
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
              fill="none"
              stroke={traceColor}
              strokeWidth="2"
              strokeLinecap="round"
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
            {edge.label && (
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
                  fill={isBranchFail ? '#F87171' : isBranchPass ? '#34D399' : '#94A3B8'}
                  fontSize="8.5"
                  fontWeight="bold"
                  fontFamily="monospace"
                  letterSpacing="0.05em"
                >
                  {edge.label}
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
