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
import { BookOpen } from 'lucide-react';
import { STEP_GUIDANCE_DATA } from '../../data/jargonDictionary';
import { JargonGlossaryModal } from './JargonGlossaryModal';

export interface OperatorGuidanceBannerProps {
  step: 1 | 2 | 3 | 4 | 5 | 6;
  className?: string;
}

/** The simple view's page header: the step bar's own name and one plain sentence about the step. */
const SIMPLE_STEP_HEADER: Record<number, { title: string; lead: string }> = {
  1: { title: '사진 준비', lead: '검사할 이미지 폴더를 불러오고, 이미지와 정상·불량 표시가 잘 들어왔는지 확인합니다.' },
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
  const [isExpanded, setIsExpanded] = useState(()=>{try{return localStorage.getItem('vision-step-guidance')==='expanded';}catch{return false;}});
  const [isGlossaryOpen, setIsGlossaryOpen] = useState(false);
  const guidance = STEP_GUIDANCE_DATA[step];

  if (!guidance) return null;
  const simple = SIMPLE_STEP_HEADER[step];

  return <>
    <header className={`modu-stage-header ${className}`}>
      <div className="min-w-0">
        <h2>{isKo ? simple.title : guidance.titleEn}</h2>
        <p>{isKo ? simple.lead : guidance.purposeEn}</p>
      </div>
      <div className="modu-stage-header__tools">
        <button type="button" className="workspace-button workspace-button--quiet" onClick={()=>setIsGlossaryOpen(true)} title={isKo?'제조 인공지능 기술 용어 사전 열기':'Open the manufacturing vision AI glossary'}><BookOpen className="h-4 w-4"/><span>{isKo?'용어 사전':'Glossary'}</span></button>
        {technicalDetails&&<button type="button" className="workspace-button workspace-button--quiet" aria-expanded={isExpanded} onClick={()=>{setIsExpanded(!isExpanded);try{localStorage.setItem('vision-step-guidance',isExpanded?'collapsed':'expanded');}catch{}}}>{isKo?(isExpanded?'안내 닫기':'단계 안내'):(isExpanded?'Close guidance':'Step guidance')}</button>}
      </div>
    </header>
    {technicalDetails&&isExpanded&&<section className="modu-stage-summary" aria-label={isKo?'단계 안내':'Step guidance'}>
      <p><strong>{isKo?'다음으로 가기 전에':'Before continuing'}</strong> · {isKo?guidance.conditionKo:guidance.conditionEn}</p>
      <p><strong>{isKo?'도움말':'Tip'}</strong> · {isKo?guidance.tipKo:guidance.tipEn}</p>
    </section>}
    <JargonGlossaryModal isOpen={isGlossaryOpen} onClose={()=>setIsGlossaryOpen(false)}/>
  </>;
};
export default OperatorGuidanceBanner;
