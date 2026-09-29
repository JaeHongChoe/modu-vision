/**
 * src/renderer/components/wizard/WizardFooter.tsx
 * 
 * Industrial Step Navigation & Guardrail Console (Keyence / Cognex Benchmark).
 * Features:
 * - High-visibility steel gray typography (0% low-contrast slate-600 text)
 * - Tactile action buttons with clear physical keyboard shortcuts (Alt+← / Alt+→, [ / ])
 * - Global keyboard navigation listener with active form input guards
 * - Central workflow monitor with tabular stage numbers
 * - Rigid industrial warning annunciator badge instead of continuous CPU animations
 */

import React, { useEffect } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
} from 'lucide-react';
import { useProjectStore } from '../../stores/useProjectStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useTrainingStore } from '../../stores/useTrainingStore';
import { useEvaluationStore } from '../../stores/useEvaluationStore';

export const WizardFooter: React.FC = () => {
  const { activeStep, setStep, language } = useProjectStore();
  const { totalImages, split } = useDatasetStore();
  const { status, jobId: trainingJobId, isCurrentData } = useTrainingStore();
  const { jobId, testPredictions, isLoading: isEvaluationLoading } = useEvaluationStore();

  const handlePrev = () => {
    if (activeStep > 1) {
      setStep((activeStep - 1) as 1 | 2 | 3 | 4 | 5 | 6);
    }
  };

  const handleNext = () => {
    if (activeStep < 6) {
      setStep((activeStep + 1) as 1 | 2 | 3 | 4 | 5 | 6);
    }
  };

  // Keyboard navigation shortcuts listener
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Guard: Do not trigger navigation if focus is inside an input, textarea, select, or editable element
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable)
      ) {
        return;
      }

      // Alt + LeftArrow OR '[' for Previous
      if (
        (e.altKey && e.key === 'ArrowLeft') ||
        (e.key === '[' && !e.ctrlKey && !e.metaKey && !e.altKey)
      ) {
        e.preventDefault();
        handlePrev();
      }

      // Alt + RightArrow OR ']' for Next
      if (
        (e.altKey && e.key === 'ArrowRight') ||
        (e.key === ']' && !e.ctrlKey && !e.metaKey && !e.altKey)
      ) {
        e.preventDefault();
        handleNext();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [activeStep]);

  const canGoNext = activeStep < 6;

  const stepTargetNamesKo = [
    '',
    'AI 오토라벨링 (2단계)',
    '오토딥러닝 학습 (3단계)',
    '품질 평가 & 과검/미검 분석 (4단계)',
    '플로우차트 체이닝 (5단계)',
    '추론 & 모델 내보내기 (6단계)',
  ];

  const stepTargetNamesEn = [
    '',
    'AI Auto-Labeling (Step 2)',
    'AutoML Training (Step 3)',
    'Evaluation & Error Analysis (Step 4)',
    'Flowchart Chaining (Step 5)',
    'Inference & Model Export (Step 6)',
  ];

  const currentStepNamesKo = [
    '데이터 관리',
    '라벨링 & AI',
    '오토딥러닝',
    '평가 & 과검/미검',
    '플로우차트',
    '추론 & 모델 내보내기',
  ];

  const currentStepNamesEn = [
    'Data Studio',
    'Labeling Studio',
    'AutoML Trainer',
    'Evaluation & Overkill',
    'Flowchart Studio',
    'Inference & Export',
  ];

  // Missing input status warning hint
  const getStepWarningHint = () => {
    if (activeStep === 1 && totalImages === 0) {
      return language === 'ko'
        ? '등록된 이미지가 없습니다 (검사 폴더를 선택하세요)'
        : 'No images loaded (select an inspection folder)';
    }
    if (activeStep === 3 && totalImages > 0 && (split.val === 0 || split.train === 0)) {
      return language === 'ko'
        ? '검증 데이터 분할이 필요합니다 (학습·검증·시험 분할 적용)'
        : 'Validation split missing (apply train/validation/test split)';
    }
    if (activeStep === 3 && status === 'running') {
      return language === 'ko'
        ? 'AutoML 학습이 백그라운드에서 진행 중입니다...'
        : 'AutoML training in progress in background...';
    }
    if (activeStep === 4 && isEvaluationLoading) {
      return language === 'ko' ? '평가 결과를 불러오는 중...' : 'Loading evaluation results...';
    }
    if (activeStep === 4 && !jobId && testPredictions.length === 0) {
      return language === 'ko'
        ? '평가할 모델이 없습니다 (3단계에서 모델을 학습하세요)'
        : 'No evaluation models available (train in Step 3)';
    }
    return null;
  };

  const warningHint = getStepWarningHint();
  const hasCurrentModel = Boolean((status === 'completed' && isCurrentData && trainingJobId) || jobId);
  const isPreviewOnly = (activeStep === 1 && totalImages === 0)
    || (activeStep === 3 && !hasCurrentModel)
    || (activeStep === 4 && !jobId)
    || (activeStep === 5 && !hasCurrentModel);

  return (
    <footer className="h-14 bg-[#0B0E14] border-t border-[#2B3547] px-6 flex items-center justify-between text-xs select-none text-slate-300">
      {/* Left: Tactile Previous Button with Keyboard Shortcut */}
      <div>
        <button
          type="button"
          onClick={handlePrev}
          disabled={activeStep === 1}
          className={`flex items-center space-x-2 px-3.5 py-2 rounded font-medium transition-colors duration-75 ${
            activeStep === 1
              ? 'text-slate-500 bg-[#131822]/60 border border-[#2B3547]/50 cursor-not-allowed'
              : 'text-slate-200 bg-[#131822] hover:bg-[#1A212E] hover:text-white border border-[#2B3547] active:bg-[#252F42] cursor-pointer'
          }`}
          title="Navigate to previous stage (Alt+← or [)"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          <span>{language === 'ko' ? '이전 단계' : 'Previous'}</span>
          <kbd
            className={`px-1.5 py-0.5 text-[10px] font-mono rounded border ${
              activeStep === 1
                ? 'bg-[#0B0E14] border-[#2B3547]/40 text-slate-500'
                : 'bg-[#0B0E14] border-[#2B3547] text-slate-400'
            }`}
          >
            Alt+←
          </kbd>
        </button>
      </div>

      {/* Middle: High-Precision Central Workflow Monitor */}
      <div className="hidden md:flex items-center space-x-2 text-xs font-mono text-slate-400">
        <span className="text-slate-500 uppercase tracking-wider text-[11px]">
          WORKFLOW:
        </span>
        <span className="px-2 py-0.5 rounded bg-[#131822] border border-[#2B3547] text-[#10B981] font-bold tabular-nums">
          STAGE 0{activeStep} / 06
        </span>
        <span className="text-slate-500">|</span>
        <span className="text-slate-200 font-semibold tracking-wide">
          {language === 'ko'
            ? currentStepNamesKo[activeStep - 1]
            : currentStepNamesEn[activeStep - 1]}
        </span>
      </div>

      {/* Right: Status Warning Annunciator & Tactile Next Button */}
      <div className="flex items-center space-x-3">
        {warningHint && (
          <div className="flex items-center space-x-1.5 px-3 py-1.5 bg-[#1A212E] border border-amber-500/40 rounded text-amber-300 text-xs font-mono">
            <AlertTriangle className="w-3.5 h-3.5 text-amber-400 shrink-0" />
            <span className="truncate max-w-sm">{warningHint}</span>
          </div>
        )}

        {activeStep < 6 ? (
          <button
            type="button"
            onClick={handleNext}
            disabled={!canGoNext}
            className="flex items-center space-x-2 px-4 py-2 rounded font-semibold bg-blue-600 hover:bg-blue-500 active:bg-blue-700 text-white border border-blue-400/30 transition-colors duration-75 cursor-pointer"
            title={isPreviewOnly ? 'Preview the next stage; its actions may require data or a completed model' : 'Advance to next stage (Alt+→ or ])'}
          >
            <span>
              {language === 'ko'
                ? `${isPreviewOnly ? '다음 단계 보기' : '다음'}: ${stepTargetNamesKo[activeStep]}`
                : `${isPreviewOnly ? 'Preview next' : 'Next'}: ${stepTargetNamesEn[activeStep]}`}
            </span>
            <kbd className="px-1.5 py-0.5 text-[10px] font-mono bg-blue-700/80 border border-blue-400/40 rounded text-blue-100">
              Alt+→
            </kbd>
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
        ) : (
          <div className="flex items-center space-x-2 px-3.5 py-2 rounded bg-slate-900 border border-slate-600 text-slate-300 font-mono font-semibold text-xs">
            <CheckCircle2 className="w-4 h-4 text-slate-400" />
            <span>
              {language === 'ko'
                ? '모델 내보내기 단계'
                : 'MODEL EXPORT STAGE'}
            </span>
          </div>
        )}
      </div>
    </footer>
  );
};
