/**
 * src/renderer/components/wizard/WizardHeader.tsx
 * 
 * Industrial Machine Vision Console Header (Cognex / Keyence Benchmark).
 * Features:
 * - Keyence physical-style hardware acceleration annunciator LED (MPS / CUDA / CPU fallback)
 * - Contiguous 6-stage segmented process bar with 1px hairline dividers and tabular-nums
 * - Industrial inspection recipe dropdown (Classification, Detection, Segmentation, Anomaly)
 * - Industrial communication daemon port monitor
 * - Zero gradients, zero diffuse glows, high-contrast dark steel styling
 */

import React from 'react';
import {
  Activity,
  ChevronDown,
  Cpu,
  Disc,
  FolderKanban,
  GitFork,
  Globe,
  Rocket,
  Scan,
  ShieldAlert,
  Tag,
  Wand2,
} from 'lucide-react';
import { useProjectStore } from '../../stores/useProjectStore';
import type { VisionTask } from '../../types';

export const WizardHeader: React.FC = () => {
  const {
    activeStep,
    setStep,
    task,
    setTask,
    language,
    setLanguage,
    backendStatus,
    projectName,
  } = useProjectStore();

  const steps = [
    { num: 1, nameKo: '데이터 관리', nameEn: 'Data Studio', icon: FolderKanban },
    { num: 2, nameKo: '라벨링 & AI', nameEn: 'Labeling Studio', icon: Tag },
    { num: 3, nameKo: '오토딥러닝', nameEn: 'AutoML Trainer', icon: Wand2 },
    { num: 4, nameKo: '평가 & 과검/미검', nameEn: 'Evaluation & Overkill', icon: Activity },
    { num: 5, nameKo: '플로우차트', nameEn: 'Flowchart Studio', icon: GitFork },
    { num: 6, nameKo: '추론 & 모델 내보내기', nameEn: 'Inference & Export', icon: Rocket },
  ];

  const tasks: Array<{
    id: VisionTask;
    code: string;
    labelKo: string;
    labelEn: string;
    icon: React.ComponentType<{ className?: string }>;
  }> = [
    {
      id: 'classification',
      code: 'CLS',
      labelKo: '분류 (Classification)',
      labelEn: 'Classification',
      icon: Tag,
    },
    {
      id: 'detection',
      code: 'DET',
      labelKo: '객체 검출 (Detection)',
      labelEn: 'Object Detection',
      icon: Scan,
    },
    {
      id: 'segmentation',
      code: 'SEG',
      labelKo: '영역 분할 (Segmentation)',
      labelEn: 'Segmentation',
      icon: Disc,
    },
    {
      id: 'anomaly',
      code: 'ANO',
      labelKo: '이상 탐지 (Anomaly)',
      labelEn: 'Anomaly Detection',
      icon: ShieldAlert,
    },
  ];

  // Resolve current task icon
  const currentTaskMeta = tasks.find((t) => t.id === task) || tasks[0];
  const CurrentTaskIcon = currentTaskMeta.icon;

  // Hardware acceleration logic
  const isHealthy = Boolean(backendStatus.healthy);
  const rawDevice = (backendStatus.device || '').toLowerCase();
  const isMps = rawDevice === 'mps';
  const isCuda = rawDevice.startsWith('cuda');
  const isAccelerated = isHealthy && (isMps || isCuda);
  const isFallback = isHealthy && (!rawDevice || rawDevice === 'cpu');

  // LED and label configuration
  const ledBgColor = isAccelerated ? '#10B981' : isFallback ? '#F59E0B' : '#EF4444';
  const ledRingClass = isAccelerated
    ? 'ring-2 ring-emerald-500/40'
    : isFallback
    ? 'ring-2 ring-amber-500/40'
    : 'ring-2 ring-red-500/40';

  const hwDisplayName = isMps
    ? 'APPLE SILICON MPS'
    : isCuda
    ? backendStatus.deviceName || 'NVIDIA CUDA'
    : isFallback
    ? 'CPU FALLBACK'
    : 'DISCONNECTED';

  const hwBadgeText = isAccelerated ? 'HW ACCEL' : isFallback ? 'FALLBACK' : 'OFFLINE';
  const hwBadgeStyle = isAccelerated
    ? 'bg-emerald-950/80 border-emerald-500/50 text-emerald-400'
    : isFallback
    ? 'bg-amber-950/80 border-amber-500/50 text-amber-300'
    : 'bg-red-950/80 border-red-500/50 text-red-400';

  return (
    <header className="bg-[#0B0E14] border-b border-[#2B3547] text-slate-200 select-none">
      {/* Top Application Bar (Draggable Electron Region) */}
      <div
        className="h-10 px-4 flex items-center justify-between border-b border-[#2B3547] bg-[#0E121A]"
        style={{ WebkitAppRegion: 'drag' } as React.CSSProperties}
      >
        {/* Left: Branding & Active Project Moniker */}
        <div
          className="flex items-center space-x-3"
          style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}
        >
          <div className="w-5 h-5 bg-[#1A212E] border border-blue-500/60 rounded flex items-center justify-center font-mono font-bold text-xs text-blue-400">
            V
          </div>
          <span className="text-xs font-bold tracking-wider text-slate-200 font-mono">
            VISION AI STUDIO
          </span>
          <span className="text-xs text-slate-500">|</span>
          <span className="text-xs text-slate-400 font-mono tracking-tight truncate max-w-xs">
            {projectName}
          </span>
        </div>

        {/* Right: Hardware Annunciator LED, Daemon Port & Controls */}
        <div
          className="flex items-center space-x-3 text-xs"
          style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}
        >
          {/* Hardware Acceleration Annunciator LED */}
          <div
            className="flex items-center space-x-2 px-2.5 py-1 rounded bg-[#131822] border border-[#2B3547] text-xs font-mono"
            title={`Compute Engine: ${hwDisplayName} | Mode: ${hwBadgeText}`}
          >
            {/* Physical-style LED with recessed dark bezel & optical ring */}
            <div className="relative flex items-center justify-center w-3.5 h-3.5 rounded-full bg-[#0B0E14] border border-[#2B3547]">
              <span
                className={`w-2 h-2 rounded-full ${ledRingClass} transition-colors duration-75`}
                style={{ backgroundColor: ledBgColor }}
              />
            </div>
            <Cpu className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-slate-300 font-semibold tracking-wide">
              {hwDisplayName}
            </span>
            <span
              className={`text-[10px] px-1.5 py-0.2 rounded border font-mono font-bold tracking-tight ${hwBadgeStyle}`}
            >
              {hwBadgeText}
            </span>
          </div>

          {/* Backend Daemon Communication Port Monitor */}
          <div className="flex items-center space-x-2 px-2.5 py-1 rounded bg-[#131822] border border-[#2B3547] font-mono">
            <span
              className={`w-2 h-2 rounded-full ${
                isHealthy
                  ? 'bg-emerald-500 ring-2 ring-emerald-500/30'
                  : 'bg-amber-400 ring-2 ring-amber-400/30 animate-pulse'
              }`}
            />
            <span className="text-slate-300">
              {isHealthy ? `PORT:${backendStatus.port}` : 'CONNECTING...'}
            </span>
          </div>

          {/* Industrial Bilingual Switcher */}
          <button
            type="button"
            onClick={() => setLanguage(language === 'ko' ? 'en' : 'ko')}
            className="flex items-center space-x-1 px-2.5 py-1 rounded bg-[#131822] hover:bg-[#1A212E] active:bg-[#252F42] border border-[#2B3547] text-slate-300 transition-colors duration-75 cursor-pointer font-mono"
            title="Toggle Language (KR / EN)"
          >
            <Globe className="w-3.5 h-3.5 text-slate-400" />
            <span className="font-bold">{language === 'ko' ? 'KR' : 'EN'}</span>
          </button>
        </div>
      </div>

      {/* Main Navigation Row: Recipe Selector & Contiguous Segmented Process Bar */}
      <div className="px-4 py-2 flex items-center justify-between bg-[#131822]">
        {/* Left: Industrial Task Recipe Selector */}
        <div className="flex items-center space-x-2">
          <span className="px-1.5 py-0.5 rounded bg-[#0B0E14] border border-[#2B3547] text-[10px] font-mono font-bold text-slate-400 uppercase tracking-wider">
            RECIPE:
          </span>
          <div className="relative flex items-center">
            <div className="pointer-events-none absolute left-2.5 flex items-center text-blue-400">
              <CurrentTaskIcon className="w-3.5 h-3.5" />
            </div>
            <select
              value={task}
              onChange={(e) => setTask(e.target.value as VisionTask)}
              className="bg-[#0B0E14] hover:bg-[#161C26] border border-[#2B3547] hover:border-slate-500 focus:border-blue-500 focus:ring-1 focus:ring-blue-500/50 text-xs font-mono font-semibold rounded pl-8 pr-8 py-1.5 text-slate-200 transition-colors duration-75 cursor-pointer appearance-none"
            >
              {tasks.map((t) => (
                <option key={t.id} value={t.id} className="bg-[#131822] text-slate-200 py-1">
                  [{t.code}] {language === 'ko' ? t.labelKo : t.labelEn}
                </option>
              ))}
            </select>
            <ChevronDown className="w-3.5 h-3.5 text-slate-400 pointer-events-none absolute right-2.5" />
          </div>
        </div>

        {/* Right: Contiguous 6-Stage Segmented Process Bar */}
        <nav
          className="inline-flex items-stretch bg-[#0B0E14] border border-[#2B3547] rounded overflow-hidden select-none"
          aria-label="Workflow Stages"
        >
          {steps.map((s) => {
            const Icon = s.icon;
            const isActive = activeStep === s.num;
            const isPrevious = activeStep > s.num;

            return (
              <button
                key={s.num}
                type="button"
                onClick={() => setStep(s.num as 1 | 2 | 3 | 4 | 5 | 6)}
                className={`relative flex items-center space-x-2 px-3.5 py-1.5 text-xs transition-colors duration-75 cursor-pointer border-r border-[#2B3547] last:border-r-0 ${
                  isActive
                    ? 'bg-[#1A212E] text-white font-bold shadow-[inset_0_2px_0_0_#10B981]'
                    : isPrevious
                    ? 'bg-[#131822] hover:bg-[#1A212E] text-slate-200'
                    : 'bg-[#0E121A] hover:bg-[#151C27] text-slate-400'
                }`}
              >
                {/* Tabular Stage Number */}
                <span
                  className={`font-mono tabular-nums text-[11px] font-bold ${
                    isActive
                      ? 'text-[#10B981]'
                      : isPrevious
                      ? 'text-slate-300'
                      : 'text-slate-500'
                  }`}
                >
                  {String(s.num).padStart(2, '0')}
                </span>

                {/* Stage Icon */}
                <Icon
                  className={`w-3.5 h-3.5 ${
                    isActive
                      ? 'text-[#10B981]'
                      : isPrevious
                      ? 'text-slate-300'
                      : 'text-slate-500'
                  }`}
                />

                {/* Stage Label */}
                <span className="whitespace-nowrap font-medium">
                  {language === 'ko' ? s.nameKo : s.nameEn}
                </span>

              </button>
            );
          })}
        </nav>
      </div>
    </header>
  );
};
