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
import { useProjectStore } from '../../stores/useProjectStore';
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

/** The simple view's page header: the step bar's own name and one plain sentence about the step. */
const SIMPLE_STEP_HEADER: Record<number, { title: string; lead: string }> = {
  1: { title: '데이터 준비', lead: '검사할 이미지 폴더를 불러오고, 이미지와 정상·불량 표시가 잘 들어왔는지 확인합니다.' },
  2: { title: '라벨 달기', lead: '이미지마다 정상인지, 불량이면 어디가 불량인지 표시합니다.' },
  3: { title: '모델 학습', lead: '모델 종류와 학습 방식을 고르고 학습을 시작합니다. 설정은 추천값 그대로 두어도 됩니다.' },
  4: { title: '결과 평가', lead: '학습한 모델이 시험 이미지를 얼마나 맞혔는지, 놓친 불량이 있는지 확인합니다.' },
  5: { title: '검사 흐름 만들기', lead: '모델과 판정 규칙을 연결해 실제 검사 순서를 만들고 저장합니다.' },
  6: { title: '검사 실행', lead: '저장한 검사 흐름으로 이미지를 검사하고 결과를 확인합니다.' },
};

export const OperatorGuidanceBanner: React.FC<OperatorGuidanceBannerProps> = ({
  step,
  className = '',
}) => {
  const isKo = useProjectStore(s=>s.language)==='ko';
  const technicalDetails = useProjectStore(s=>s.technicalDetails);
  // Only an explicit simple view uses the plain page header; callers without a view mode keep the guidance strip.
  const simpleView = useProjectStore(s=>s.viewMode)==='simple';
  const [isExpanded, setIsExpanded] = useState(()=>{try{return localStorage.getItem('vision-step-guidance')==='expanded';}catch{return false;}});
  const [isGlossaryOpen, setIsGlossaryOpen] = useState(false);
  const guidance = STEP_GUIDANCE_DATA[step];

  if (!guidance) return null;
  const simple = SIMPLE_STEP_HEADER[step];
  if (simpleView && isKo && simple) {
    return (
      <>
        <header className={`flex items-start justify-between gap-4 border-b border-[#2B3547] bg-[#131822] px-6 py-4 select-none ${className}`}>
          <div className="min-w-0">
            <p className="text-xs font-semibold text-cyan-300">{step}단계</p>
            <h2 className="text-xl font-bold leading-7 text-slate-50">{simple.title}</h2>
            <p className="mt-1 max-w-3xl text-sm text-slate-400">{simple.lead}</p>
          </div>
          <button type="button" onClick={() => setIsGlossaryOpen(true)}
            className="flex shrink-0 items-center gap-1.5 rounded border border-[#2B3547] px-2.5 py-1 text-xs text-slate-300 hover:bg-[#1A212E]"
            title="제조 인공지능 기술 용어 사전 열기">
            <BookOpen className="h-3.5 w-3.5 text-slate-400" />
            <span>용어 사전</span>
          </button>
        </header>
        <JargonGlossaryModal isOpen={isGlossaryOpen} onClose={() => setIsGlossaryOpen(false)} />
      </>
    );
  }

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
              STEP {step}: {isKo ? guidance.titleKo : guidance.titleEn}
            </span>
            {technicalDetails && <span className="text-slate-500 font-mono text-[11px]">[{isKo ? guidance.titleEn : guidance.titleKo}]</span>}
          </div>

          <div className="flex items-center space-x-2.5">
            {/* Glossary Quick Button */}
            <button
              type="button"
              onClick={() => setIsGlossaryOpen(true)}
              className="flex items-center space-x-1.5 px-2.5 py-1 bg-[#1A212E] hover:bg-[#2B3547] text-slate-300 hover:text-white border border-[#2B3547] rounded text-[11px] font-medium transition-colors cursor-pointer"
              title={isKo ? '제조 인공지능 기술 용어 사전 열기' : 'Open the manufacturing vision AI glossary'}
            >
              <BookOpen className="w-3.5 h-3.5 text-slate-400" />
              <span>{isKo ? '용어 사전' : 'Glossary'}</span>
            </button>

            {/* Toggle Expand / Collapse */}
            <button
              type="button"
              aria-expanded={isExpanded} onClick={() => {setIsExpanded(!isExpanded);try{localStorage.setItem('vision-step-guidance',isExpanded?'collapsed':'expanded');}catch{}}}
              className="flex items-center space-x-1 px-2 py-1 rounded text-slate-400 hover:text-slate-200 hover:bg-[#1A212E] border border-transparent hover:border-[#2B3547] transition-colors cursor-pointer text-[11px]"
            >
              <span>{isKo ? (isExpanded ? '가이드 접기' : '가이드 펼치기') : (isExpanded ? 'Collapse guidance' : 'Expand guidance')}</span>
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
                  <span>{isKo ? '현재 단계의 목적' : 'Purpose of this step'}</span>
                </div>
                <p className="text-slate-300 leading-relaxed text-[11px] font-sans">
                  {isKo ? guidance.purposeKo : guidance.purposeEn}
                </p>
              </div>
            </div>

            {/* Pillar 2: 다음 단계 진행 조건 */}
            <div className="p-3 bg-[#1A212E] rounded border border-[#2B3547] flex flex-col justify-between">
              <div>
                <div className="flex items-center space-x-1.5 font-bold text-slate-200 mb-1.5 text-[11px] uppercase tracking-wide">
                  <Zap className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                  <span>{isKo ? '다음 단계 진행 조건' : 'Before continuing'}</span>
                </div>
                <p className="text-slate-300 leading-relaxed text-[11px] font-sans">
                  {isKo ? guidance.conditionKo : guidance.conditionEn}
                </p>
              </div>
            </div>

            {/* Pillar 3: 비전문가를 위한 실무 팁 */}
            <div className="p-3 bg-[#1A212E] rounded border border-[#2B3547] flex flex-col justify-between">
              <div>
                <div className="flex items-center space-x-1.5 font-bold text-amber-400 mb-1.5 text-[11px] uppercase tracking-wide">
                  <Lightbulb className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                  <span>{isKo ? '비전문가를 위한 실무 팁' : 'Practical tip'}</span>
                </div>
                <p className="text-slate-300 leading-relaxed text-[11px] font-sans">
                  {isKo ? guidance.tipKo : guidance.tipEn}
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
