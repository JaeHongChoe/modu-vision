/**
 * src/renderer/components/common/OperatorGuidanceBanner.tsx
 * Top guidance banner for shop-floor operators across the 6 wizard steps.
 * Features:
 * - 🎯 현재 단계의 목적
 * - ⚡ 다음 단계 진행 조건
 * - 💡 비전문가를 위한 실무 팁
 * - Collapsible toggle (접기 / 펼치기)
 * - Quick Glossary Modal trigger (📖 용어 사전)
 */

import React, { useState } from 'react';
import {
  ChevronDown,
  ChevronUp,
  Target,
  Zap,
  Lightbulb,
  BookOpen,
} from 'lucide-react';
import { STEP_GUIDANCE_DATA } from '../../data/jargonDictionary';
import { JargonGlossaryModal } from './JargonGlossaryModal';
import { LedAnnunciator } from './LedAnnunciator';

export interface OperatorGuidanceBannerProps {
  step: 1 | 2 | 3 | 4 | 5 | 6;
  className?: string;
}

export const OperatorGuidanceBanner: React.FC<OperatorGuidanceBannerProps> = ({
  step,
  className = '',
}) => {
  const [isExpanded, setIsExpanded] = useState(true);
  const [isGlossaryOpen, setIsGlossaryOpen] = useState(false);
  const guidance = STEP_GUIDANCE_DATA[step];

  if (!guidance) return null;

  return (
    <>
      <div
        className={`bg-[#131822] border-b border-[#2B3547] text-slate-200 select-none transition-colors ${className}`}
      >
        {/* Banner Top Control Strip */}
        <div className="px-5 py-2 flex items-center justify-between text-xs border-b border-[#2B3547] bg-[#0B0E14]">
          <div className="flex items-center space-x-2.5">
            <LedAnnunciator state="running" size="sm" />
            <span className="font-bold text-slate-100 font-mono text-xs uppercase tracking-wide">
              STEP {step}: {guidance.titleKo}
            </span>
            <span className="text-slate-500 font-mono text-[11px]">[{guidance.titleEn}]</span>
          </div>

          <div className="flex items-center space-x-2.5">
            {/* Glossary Quick Button */}
            <button
              type="button"
              onClick={() => setIsGlossaryOpen(true)}
              className="flex items-center space-x-1.5 px-2.5 py-1 bg-[#1A212E] hover:bg-[#2B3547] text-slate-300 hover:text-white border border-[#2B3547] rounded text-[11px] font-medium transition-colors cursor-pointer"
              title="제조 인공지능 기술 용어 사전 열기"
            >
              <BookOpen className="w-3.5 h-3.5 text-slate-400" />
              <span>용어 사전</span>
            </button>

            {/* Toggle Expand / Collapse */}
            <button
              type="button"
              onClick={() => setIsExpanded(!isExpanded)}
              className="flex items-center space-x-1 px-2 py-1 rounded text-slate-400 hover:text-slate-200 hover:bg-[#1A212E] border border-transparent hover:border-[#2B3547] transition-colors cursor-pointer text-[11px]"
            >
              <span>{isExpanded ? '가이드 접기' : '가이드 펼치기'}</span>
              {isExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
            </button>
          </div>
        </div>

        {/* 3-Pillars Card Body */}
        {isExpanded && (
          <div className="px-5 py-3 grid grid-cols-1 md:grid-cols-3 gap-3 text-xs bg-[#131822] animate-in fade-in duration-100">
            {/* Pillar 1: 현재 단계의 목적 */}
            <div className="p-3 bg-[#1A212E] rounded border border-[#2B3547] flex flex-col justify-between">
              <div>
                <div className="flex items-center space-x-1.5 font-bold text-slate-200 mb-1.5 text-[11px] uppercase tracking-wide">
                  <Target className="w-3.5 h-3.5 text-blue-400 shrink-0" />
                  <span>현재 단계의 목적</span>
                </div>
                <p className="text-slate-300 leading-relaxed text-[11px] font-sans">
                  {guidance.purposeKo}
                </p>
              </div>
            </div>

            {/* Pillar 2: 다음 단계 진행 조건 */}
            <div className="p-3 bg-[#1A212E] rounded border border-[#2B3547] flex flex-col justify-between">
              <div>
                <div className="flex items-center space-x-1.5 font-bold text-slate-200 mb-1.5 text-[11px] uppercase tracking-wide">
                  <Zap className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                  <span>다음 단계 진행 조건</span>
                </div>
                <p className="text-slate-300 leading-relaxed text-[11px] font-sans">
                  {guidance.conditionKo}
                </p>
              </div>
            </div>

            {/* Pillar 3: 비전문가를 위한 실무 팁 */}
            <div className="p-3 bg-[#1A212E] rounded border border-[#2B3547] flex flex-col justify-between">
              <div>
                <div className="flex items-center space-x-1.5 font-bold text-amber-400 mb-1.5 text-[11px] uppercase tracking-wide">
                  <Lightbulb className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                  <span>비전문가를 위한 실무 팁</span>
                </div>
                <p className="text-slate-300 leading-relaxed text-[11px] font-sans">
                  {guidance.tipKo}
                </p>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Global Glossary Modal */}
      <JargonGlossaryModal
        isOpen={isGlossaryOpen}
        onClose={() => setIsGlossaryOpen(false)}
      />
    </>
  );
};

export default OperatorGuidanceBanner;
