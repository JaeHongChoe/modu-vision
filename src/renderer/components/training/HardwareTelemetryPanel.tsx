/**
 * src/renderer/components/training/HardwareTelemetryPanel.tsx
 * Keyence CV-X / XG-X Hardware Diagnostic Level Meter Panel.
 * Features 16-segment discrete LED ladder level meters for VRAM (X.XX GB / Y.YY GB),
 * Host CPU load %, and System RAM %, plus Apple Silicon MPS / NVIDIA CUDA / CPU annunciators,
 * and 10.0 Hz Stream Locked badge with strict tabular-nums.
 */

import React from 'react';
import { Cpu, HardDrive, Zap } from 'lucide-react';
import type { HardwareStats } from '../../types';
import { LedAnnunciator } from '../common/LedAnnunciator';

export interface HardwareTelemetryPanelProps {
  hardware: HardwareStats;
  isTraining: boolean;
  language: 'ko' | 'en';
}

/**
 * Keyence 16-Segment Discrete LED Ladder Level Meter Component
 */
const Keyence16SegmentMeter: React.FC<{
  percent: number;
  label: string;
  readout: string;
  subtext?: string;
  icon: React.ReactNode;
}> = ({ percent, label, readout, subtext, icon }) => {
  const totalSegments = 16;
  const clampedPercent = Math.min(100, Math.max(0, percent));
  const activeCount = Math.round((clampedPercent / 100) * totalSegments);

  return (
    <div className="space-y-1.5 select-none">
      <div className="flex items-center justify-between text-xs">
        <div className="flex items-center space-x-1.5 text-slate-300 font-semibold text-[11px]">
          {icon}
          <span>{label}</span>
        </div>
        <span className="font-mono tabular-nums font-bold text-slate-100 text-xs bg-[#0B0E14] px-1.5 py-0.5 rounded-[2px] border border-[#2B3547]">
          {readout}
        </span>
      </div>

      {/* Discrete 16-Segment Ladder Grid */}
      <div className="grid grid-cols-[repeat(16,minmax(0,1fr))] gap-[2px] p-1 bg-[#070A0F] border border-[#1E293B] rounded-[2px]">
        {Array.from({ length: totalSegments }, (_, i) => {
          const isActive = i < activeCount;
          // Segments 0-9: Emerald, 10-12: Amber, 13-15: Crimson
          let activeColor = 'bg-[#10B981]';
          let inactiveColor = 'bg-[#0A261B]';

          if (i >= 13) {
            activeColor = 'bg-[#EF4444]';
            inactiveColor = 'bg-[#290E0E]';
          } else if (i >= 10) {
            activeColor = 'bg-[#F59E0B]';
            inactiveColor = 'bg-[#261B0A]';
          }

          return (
            <div
              key={i}
              className={`h-3 rounded-[1px] transition-colors duration-75 ${
                isActive ? activeColor : inactiveColor
              }`}
            />
          );
        })}
      </div>

      {subtext && (
        <div className="flex justify-between text-[10px] font-mono text-slate-400">
          <span className="truncate">{subtext}</span>
          <span className="tabular-nums font-semibold">{clampedPercent.toFixed(1)}%</span>
        </div>
      )}
    </div>
  );
};

export const HardwareTelemetryPanel: React.FC<HardwareTelemetryPanelProps> = ({
  hardware,
  isTraining,
}) => {
  // Format VRAM memory
  const usedMb = hardware.gpu_memory_used_mb || 0;
  const usedGb = (usedMb / 1024).toFixed(2);
  const totalGbEstimate = 16.0; // Standard industrial workstation unified memory reference
  const vramPercent = Math.min(100, (usedMb / (totalGbEstimate * 1024)) * 100);

  // Hardware accelerator LED classification
  const getDeviceStatus = () => {
    const dt = (hardware.device_type || 'cpu').toLowerCase();
    if (dt === 'mps') {
      return {
        state: 'running' as const,
        label: 'APPLE SILICON MPS',
        value: 'Metal 3.1 Unified',
      };
    }
    if (dt === 'cuda') {
      return {
        state: 'pass' as const,
        label: 'NVIDIA CUDA',
        value: 'Tensor Core Active',
      };
    }
    return {
      state: 'standby' as const,
      label: 'CPU HOST INFERENCE',
      value: 'AVX-512 Fallback',
    };
  };

  const devStatus = getDeviceStatus();

  return (
    <div className="bg-[#131822] border border-[#2B3547] rounded-[4px] p-4 flex flex-col justify-between space-y-4 select-none">
      {/* Top Header: Acceleration Annunciator & Lock Status */}
      <div className="space-y-2 pb-3 border-b border-[#1E293B]">
        <div className="flex items-center justify-between">
          <span className="font-mono text-[11px] font-bold text-slate-200 tracking-wider">
            HARDWARE TELEMETRY
          </span>
          <span className="inline-flex items-center space-x-1 px-1.5 py-0.5 rounded-[2px] bg-[#0B0E14] border border-[#2B3547] text-[9px] font-mono tabular-nums text-emerald-400 font-bold">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
            <span>10.0 Hz STREAM LOCKED</span>
          </span>
        </div>

        {/* Primary Hardware Status LED */}
        <div className="flex items-center justify-between p-2 bg-[#0B0E14] border border-[#1E293B] rounded-[3px]">
          <LedAnnunciator
            state={devStatus.state}
            size="md"
            label={devStatus.label}
            value={devStatus.value}
            pulse={isTraining}
          />
        </div>
      </div>

      {/* Discrete 16-Segment LED Meters */}
      <div className="space-y-4">
        {/* VRAM / Unified Memory */}
        <Keyence16SegmentMeter
          percent={vramPercent}
          label="VRAM / UNIFIED MEMORY"
          readout={`${usedGb} GB / ${totalGbEstimate.toFixed(2)} GB`}
          subtext={hardware.gpu_name || 'Hardware Accelerator'}
          icon={<HardDrive className="w-3.5 h-3.5 text-blue-400" />}
        />

        {/* Host CPU Utilization */}
        <Keyence16SegmentMeter
          percent={hardware.cpu_percent}
          label="HOST CPU LOAD"
          readout={`${hardware.cpu_percent.toFixed(1)}%`}
          subtext="Worker Threads Allocation"
          icon={<Cpu className="w-3.5 h-3.5 text-emerald-400" />}
        />

        {/* System RAM Allocation */}
        <Keyence16SegmentMeter
          percent={hardware.memory_percent}
          label="SYSTEM RAM ALLOCATION"
          readout={`${hardware.memory_percent.toFixed(1)}%`}
          subtext="System Memory & Page Cache"
          icon={<Zap className="w-3.5 h-3.5 text-amber-400" />}
        />
      </div>

      {/* Bottom Diagnostic Terminal Card */}
      <div className="p-2.5 bg-[#0B0E14] border border-[#1E293B] rounded-[3px] text-[10px] font-mono text-slate-400 space-y-1">
        <div className="flex justify-between">
          <span className="text-slate-500">ACCELERATOR:</span>
          <span className="text-slate-200 font-bold uppercase">{hardware.device_type}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">STREAM PROTOCOL:</span>
          <span className="text-emerald-400 font-bold">WS-JSON @ 10-50Hz</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">PRECISION MODE:</span>
          <span className="text-slate-200 font-bold">AMP FP16 / FP32 Safe</span>
        </div>
      </div>
    </div>
  );
};
