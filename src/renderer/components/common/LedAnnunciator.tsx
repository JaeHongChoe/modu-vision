/**
 * src/renderer/components/common/LedAnnunciator.tsx
 * Discrete Hardware LED Annunciator Component.
 * Supports PASS (#10B981), FAIL (#EF4444), STANDBY (#F59E0B), RUNNING (#3B82F6), OFFLINE (#4B5563).
 */

import React from 'react';

export type LedState = 'pass' | 'fail' | 'standby' | 'running' | 'offline';
export type LedSize = 'sm' | 'md' | 'lg';

export interface LedAnnunciatorProps {
  state: LedState;
  size?: LedSize;
  label?: React.ReactNode;
  value?: string | number;
  unit?: string;
  labelPosition?: 'left' | 'right';
  pulse?: boolean;
  className?: string;
  title?: string;
}

const LED_CONFIG: Record<
  LedState,
  {
    bg: string;
    ring: string;
    innerGlow: string;
    text: string;
    defaultLabel: string;
  }
> = {
  pass: {
    bg: 'bg-[#10B981]',
    ring: 'ring-[#059669]/60',
    innerGlow: 'bg-[#34D399]',
    text: 'text-[#10B981]',
    defaultLabel: 'PASS',
  },
  fail: {
    bg: 'bg-[#EF4444]',
    ring: 'ring-[#DC2626]/60',
    innerGlow: 'bg-[#F87171]',
    text: 'text-[#EF4444]',
    defaultLabel: 'FAIL',
  },
  standby: {
    bg: 'bg-[#F59E0B]',
    ring: 'ring-[#D97706]/60',
    innerGlow: 'bg-[#FBBF24]',
    text: 'text-[#F59E0B]',
    defaultLabel: 'STANDBY',
  },
  running: {
    bg: 'bg-[#3B82F6]',
    ring: 'ring-[#2563EB]/60',
    innerGlow: 'bg-[#60A5FA]',
    text: 'text-[#3B82F6]',
    defaultLabel: 'RUNNING',
  },
  offline: {
    bg: 'bg-[#4B5563]',
    ring: 'ring-[#374151]/60',
    innerGlow: 'bg-[#6B7280]',
    text: 'text-[#9CA3AF]',
    defaultLabel: 'OFFLINE',
  },
};

const SIZE_CONFIG: Record<
  LedSize,
  {
    dot: string;
    bezel: string;
    textSize: string;
    valueSize: string;
  }
> = {
  sm: {
    dot: 'w-[6px] h-[6px]',
    bezel: 'p-[1px]',
    textSize: 'text-[10px]',
    valueSize: 'text-[11px]',
  },
  md: {
    dot: 'w-[8px] h-[8px]',
    bezel: 'p-[1.5px]',
    textSize: 'text-[11px]',
    valueSize: 'text-xs',
  },
  lg: {
    dot: 'w-[10px] h-[10px]',
    bezel: 'p-[2px]',
    textSize: 'text-xs',
    valueSize: 'text-sm',
  },
};

export const LedAnnunciator: React.FC<LedAnnunciatorProps> = ({
  state,
  size = 'md',
  label,
  value,
  unit,
  labelPosition = 'right',
  pulse = false,
  className = '',
  title,
}) => {
  const cfg = LED_CONFIG[state] || LED_CONFIG.offline;
  const sz = SIZE_CONFIG[size] || SIZE_CONFIG.md;
  const shouldPulse = pulse || (state === 'running');

  // Recessed physical LED element
  const ledDot = (
    <div
      className={`relative inline-flex items-center justify-center shrink-0 rounded-full bg-[#0B0E14] ring-1 ring-[#2B3547] ${sz.bezel}`}
      title={title || label?.toString() || cfg.defaultLabel}
    >
      <span
        className={`relative inline-block rounded-full ${sz.dot} ${cfg.bg} ${
          shouldPulse ? 'animate-pulse' : ''
        }`}
      >
        {/* Subtle physical specular top highlight */}
        <span className="absolute top-0 left-1/4 w-1/2 h-1/3 bg-white/40 rounded-full blur-[0.3px]" />
      </span>
    </div>
  );

  const hasContent = label !== undefined || value !== undefined || unit !== undefined;

  if (!hasContent) {
    return <span className={`inline-flex items-center ${className}`}>{ledDot}</span>;
  }

  const textBlock = (
    <div className={`flex items-baseline space-x-1.5 ${sz.textSize} select-none`}>
      {label && <span className="font-semibold text-slate-300 tracking-wide uppercase">{label}</span>}
      {value !== undefined && (
        <span className={`font-mono tabular-nums font-bold text-slate-100 ${sz.valueSize}`}>
          {value}
        </span>
      )}
      {unit && (
        <span className="font-mono text-[10px] text-slate-400 uppercase tracking-tight">
          {unit}
        </span>
      )}
    </div>
  );

  return (
    <div className={`inline-flex items-center space-x-2 ${className}`}>
      {labelPosition === 'left' && textBlock}
      {ledDot}
      {labelPosition === 'right' && textBlock}
    </div>
  );
};

export default LedAnnunciator;
