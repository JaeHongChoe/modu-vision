/**
 * src/renderer/components/common/ErrorModal.tsx
 * Bilingual (KR/EN) fault dialog with explicit next-training and navigation actions.
 * Supports ERR_001 through ERR_008.
 */

import React, {useEffect, useId, useRef, useState} from 'react';
import { X, Wrench } from 'lucide-react';
import { useProjectStore } from '../../stores/useProjectStore';
import { useTrainingStore } from '../../stores/useTrainingStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { LedAnnunciator } from './LedAnnunciator';
import {useComputeStore} from '../../stores/useComputeStore';
import {performErrorAction, errorActionLabels, trainingPresetBatchSize} from './errorActions';

export const ErrorModal: React.FC = () => {
  const { activeError, clearError, language, setLanguage } = useProjectStore();
  const training = useTrainingStore();
  const [busy,setBusy] = useState(false);
  const [actionError,setActionError] = useState('');
  const { setShowGeneratorModal } = useDatasetStore();
  const titleId = useId();
  const dialog = useRef<HTMLDivElement>(null);
  // A fault dialog takes focus and closes on Escape, like the other dialogs; nothing behind it is reachable by Tab.
  useEffect(() => {
    if (!activeError) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); clearError(); return; }
      if (event.key !== 'Tab' || !dialog.current) return;
      const controls = Array.from(dialog.current.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input, select, textarea'));
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); previous?.focus?.({ preventScroll: true }); };
  }, [activeError, clearError]);

  if (!activeError) return null;

  const isKo = language === 'ko';
  const title = isKo ? activeError.title_kr || activeError.title_ko || activeError.title_en : activeError.title_en;
  const desc = isKo
    ? activeError.description_kr || activeError.message_ko || activeError.description_en
    : activeError.description_en || activeError.message_en;
  const cause = isKo ? activeError.cause_kr || activeError.cause_en : activeError.cause_en;
  const remed = isKo
    ? activeError.remediation_kr || activeError.remediation || activeError.remediation_en
    : activeError.remediation_en;

  const actionLabel = errorActionLabels[activeError.action || ''];
  const handleRemediate = async () => {
    setBusy(true); setActionError('');
    const before=useProjectStore.getState();
    const projectScope=`${before.projectDir}:${before.project?.source_dataset_dir}:${before.project?.active_labelset_id}:${useComputeStore.getState().transportRevision}`;
    const isCurrent=()=>{const now=useProjectStore.getState();return projectScope===`${now.projectDir}:${now.project?.source_dataset_dir}:${now.project?.active_labelset_id}:${useComputeStore.getState().transportRevision}`;};
    try {
      const result = await performErrorAction(activeError.action, {
        isCurrent,
        settings: {batchSize: training.nextBatchSize ?? trainingPresetBatchSize(training.preset), device: training.nextDevice || 'auto'},
        setSettings: training.setNextSettings,
        selectLocal: () => useComputeStore.getState().selectTarget(null),
        readTarget: () => useComputeStore.getState().selectedProfileId,
        openData: () => useProjectStore.getState().setStep(1),
        openSynthetic: () => setShowGeneratorModal(true),
      });
      if(!isCurrent())return;
      training.setRemediationNotice(result.message);
      if (result.effect === 'navigation') clearError();
    } catch (cause) {setActionError(cause instanceof Error ? cause.message : String(cause));}
    finally {setBusy(false);}
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4 select-none animate-in fade-in duration-100">
      <div ref={dialog} role="dialog" aria-modal="true" aria-labelledby={titleId} className="bg-[#131822] border-2 border-[#EF4444] rounded max-w-lg w-full text-slate-200 shadow-2xl overflow-hidden">
        {/* Industrial Alarm Header Bar */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-[#EF4444]/60 bg-[#250D11]">
          <div className="flex items-center space-x-2.5">
            <LedAnnunciator state="fail" size="md" pulse />
            <span className="px-2 py-0.5 bg-[#3D1016] text-[#EF4444] border border-[#EF4444]/60 rounded font-mono font-bold text-xs tabular-nums">
              {activeError.code}
            </span>
            <h3 id={titleId} className="font-bold text-sm text-red-100 flex items-center space-x-1.5">
              <span>{title}</span>
            </h3>
          </div>
          <button
            onClick={clearError}
            aria-label="닫기"
            className="text-red-300 hover:text-white p-1 rounded hover:bg-[#3D1016] transition-colors cursor-pointer"
            title="닫기 (Esc)"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Diagnostics & Remediation Body */}
        <div className="p-5 space-y-3 text-sm bg-[#131822]">
          {/* Explanation */}
          <div className="p-3 bg-[#0B0E14] rounded border border-[#2B3547]">
            <span className="text-slate-400 block font-semibold text-xs mb-1">
              {isKo ? '무슨 일이 있었나요' : 'What happened'}
            </span>
            <p className="text-slate-200 leading-relaxed font-sans text-sm">{desc}</p>
          </div>

          {/* Root Cause */}
          {cause && (
            <div className="p-3 bg-[#0B0E14] rounded border border-[#2B3547]">
              <span className="text-amber-400 block font-semibold text-xs mb-1">
                {isKo ? '원인으로 보이는 것' : 'Likely cause'}
              </span>
              <p className="text-slate-300 leading-relaxed font-sans text-sm">{cause}</p>
            </div>
          )}

          {/* Remediation */}
          <div className="p-3 bg-[#0E2018] rounded border border-[#10B981]/50">
            <span className="text-[#10B981] block font-semibold text-xs mb-1">
              {isKo ? '이렇게 해 보세요' : 'What to try'}
            </span>
            <p className="text-emerald-100 leading-relaxed font-sans text-sm">{remed}</p>
          </div>
        </div>

        {actionError && <p role="alert" className="px-5 pb-3 text-sm text-red-300">{actionError}</p>}
        {training.remediationNotice && <p role="status" className="px-5 pb-3 text-sm text-emerald-200">{training.remediationNotice}</p>}
        {/* Action Controls */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-[#2B3547] bg-[#0B0E14]">
          <button
            onClick={() => setLanguage(isKo ? 'en' : 'ko')}
            className="text-xs font-mono text-slate-400 hover:text-slate-200 underline cursor-pointer transition-colors"
          >
            {isKo ? 'Switch to English' : '한국어로 보기'}
          </button>

          <div className="flex space-x-2">
            <button
              onClick={clearError}
              className="px-3.5 py-1.5 rounded bg-[#1A212E] hover:bg-[#2B3547] border border-[#2B3547] text-xs font-medium text-slate-300 cursor-pointer transition-colors"
            >
              {isKo ? '닫기' : 'Dismiss'}
            </button>

            {actionLabel && (
              <button
                onClick={() => void handleRemediate()}
                disabled={busy}
                className="flex items-center space-x-1.5 px-4 py-1.5 rounded bg-[#2563EB] hover:bg-[#1D4ED8] active:bg-[#1E40AF] text-xs font-bold text-white border border-blue-400/40 cursor-pointer transition-colors"
              >
                <Wrench className="w-3.5 h-3.5" />
                <span>{busy ? (isKo ? '적용 확인 중…' : 'Checking…') : actionLabel[isKo ? 0 : 1]}</span>
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default ErrorModal;
