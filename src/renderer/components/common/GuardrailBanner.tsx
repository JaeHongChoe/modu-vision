/**
 * src/renderer/components/common/GuardrailBanner.tsx
 * In-Page Inline Guardrail UI & 1-Click Remediation Component (Feature F32).
 * Renders contextual non-intimidating banners when essential workflow inputs
 * or prerequisites are missing, providing prominent 1-click remediation actions.
 */

import React, { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  AlertTriangle,
  Info,
  ShieldAlert,
  CheckCircle2,
  Loader2,
  LucideIcon,
  Lightbulb,
} from 'lucide-react';
import { useProjectStore } from '../../stores/useProjectStore';
import { getProjectContextGeneration, subscribeProjectContext } from '../../services/api';
import { hostErrorMessage } from '../../services/hostAdapter';

export interface GuardrailAction {
  label: string;
  onClick: () => Promise<void> | void;
  icon?: LucideIcon;
  variant?: 'primary' | 'secondary' | 'accent' | 'danger';
  loadingText?: string;
}

export interface GuardrailBannerProps {
  type?: 'warning' | 'info' | 'critical' | 'success';
  title: string;
  description: string;
  shopFloorTip?: string;
  stepContext?: string;
  actions: GuardrailAction[];
  className?: string;
}

export const GuardrailBanner: React.FC<GuardrailBannerProps> = ({
  type = 'warning',
  title,
  description,
  shopFloorTip,
  stepContext,
  actions,
  className = '',
}) => {
  const language = useProjectStore(state => state.language);
  const projectId = useProjectStore(state => state.project?.id);
  const projectDir = useProjectStore(state => state.projectDir);
  const task = useProjectStore(state => state.task);
  const sourceDir = useProjectStore(state => state.project?.source_dataset_dir);
  const labelsetId = useProjectStore(state => state.project?.active_labelset_id);
  const authority = useSyncExternalStore(
    subscribeProjectContext, getProjectContextGeneration, getProjectContextGeneration,
  );
  const projectScope = JSON.stringify([projectId, projectDir, task, sourceDir, labelsetId]);
  // Callbacks are recreated by parents during progress updates. Bind feedback to the
  // actual project/authority and visible action context, rather than function identity.
  const contextKey = JSON.stringify([
    projectScope, authority, language, type, title, description, shopFloorTip, stepContext,
    actions.map(action => [action.label, action.variant, action.loadingText]),
  ]);
  const owner = useRef({ key: contextKey, revision: 0 });
  if (owner.current.key !== contextKey) {
    owner.current = { key: contextKey, revision: owner.current.revision + 1 };
  }
  const revision = owner.current.revision;
  const mounted = useRef(true);
  const activeRequest = useRef<{ revision: number } | null>(null);
  const [feedback, setFeedback] = useState<{
    revision: number; loadingIndex: number | null; error: string;
  } | null>(null);
  const loadingIndex = feedback?.revision === revision ? feedback.loadingIndex : null;
  const error = feedback?.revision === revision ? feedback.error : '';
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      activeRequest.current = null;
    };
  }, []);

  const ownsContext = () => {
    const current = useProjectStore.getState();
    return mounted.current && owner.current.revision === revision
      && getProjectContextGeneration() === authority
      && JSON.stringify([current.project?.id, current.projectDir, current.task,
        current.project?.source_dataset_dir, current.project?.active_labelset_id]) === projectScope;
  };

  const handleActionClick = async (action: GuardrailAction, index: number) => {
    if (!ownsContext() || activeRequest.current?.revision === revision) return;
    const request = { revision };
    activeRequest.current = request;
    const current = () => ownsContext() && activeRequest.current === request;
    setFeedback({ revision, loadingIndex: index, error: '' });
    try {
      await action.onClick();
    } catch (err) {
      console.error('Guardrail remediation action failed:', err);
      if (current()) {
        const fallback = language === 'ko'
          ? '원인을 확인한 뒤 다시 시도하세요.' : 'Check the cause before trying again.';
        const message = err instanceof Error && err.message === 'Approve this web account in the local host dialog first'
          ? language === 'ko'
            ? '로컬 보조 프로그램의 승인 창에서 이 웹 계정을 먼저 승인하세요.'
            : 'Approve this web account in the local host dialog first.'
          : hostErrorMessage(err, fallback);
        const prefix = language === 'ko' ? '작업을 완료하지 못했습니다.' : 'Could not complete this action.';
        setFeedback({ revision, loadingIndex: index, error: `${prefix} ${message}` });
      }
    } finally {
      if (current()) {
        activeRequest.current = null;
        setFeedback(previous => previous?.revision === revision
          ? { ...previous, loadingIndex: null } : previous);
      }
    }
  };

  // Solid industrial color schemes
  const colorMap = {
    warning: {
      container: 'bg-[#18140E] border-[#F59E0B]/60 text-amber-200',
      badge: 'bg-[#291E10] text-[#F59E0B] border-[#F59E0B]/50',
      iconBg: 'bg-[#291E10] text-[#F59E0B] border-[#F59E0B]/50',
      primaryBtn:
        'bg-[#F59E0B] hover:bg-[#D97706] active:bg-[#B45309] text-slate-950 font-bold border border-[#F59E0B]',
      secondaryBtn: 'bg-[#131822] text-amber-300 hover:bg-[#1A212E] border-[#F59E0B]/40',
      icon: AlertTriangle,
    },
    critical: {
      container: 'bg-[#1A0E11] border-[#EF4444]/60 text-rose-200',
      badge: 'bg-[#311117] text-[#EF4444] border-[#EF4444]/50',
      iconBg: 'bg-[#311117] text-[#EF4444] border-[#EF4444]/50',
      primaryBtn:
        'bg-[#EF4444] hover:bg-[#DC2626] active:bg-[#B91C1C] text-white font-bold border border-[#EF4444]',
      secondaryBtn: 'bg-[#131822] text-rose-300 hover:bg-[#1A212E] border-[#EF4444]/40',
      icon: ShieldAlert,
    },
    info: {
      container: 'bg-[#0E1524] border-[#3B82F6]/60 text-blue-200',
      badge: 'bg-[#142338] text-[#3B82F6] border-[#3B82F6]/50',
      iconBg: 'bg-[#142338] text-[#3B82F6] border-[#3B82F6]/50',
      primaryBtn:
        'bg-[#3B82F6] hover:bg-[#2563EB] active:bg-[#1D4ED8] text-white font-bold border border-[#3B82F6]',
      secondaryBtn: 'bg-[#131822] text-blue-300 hover:bg-[#1A212E] border-[#3B82F6]/40',
      icon: Info,
    },
    success: {
      container: 'bg-[#0D1C16] border-[#10B981]/60 text-emerald-200',
      badge: 'bg-[#132E23] text-[#10B981] border-[#10B981]/50',
      iconBg: 'bg-[#132E23] text-[#10B981] border-[#10B981]/50',
      primaryBtn:
        'bg-[#10B981] hover:bg-[#059669] active:bg-[#047857] text-slate-950 font-bold border border-[#10B981]',
      secondaryBtn: 'bg-[#131822] text-emerald-300 hover:bg-[#1A212E] border-[#10B981]/40',
      icon: CheckCircle2,
    },
  };

  const scheme = colorMap[type];
  const IconComponent = scheme.icon;

  return (
    <div
      className={`rounded border p-3.5 mb-3.5 transition-all select-none ${scheme.container} ${className}`}
    >
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
        {/* Left: Icon & Explanations */}
        <div className="flex items-start space-x-3 flex-1">
          <div
            className={`p-2 rounded border flex-shrink-0 flex items-center justify-center ${scheme.iconBg}`}
          >
            <IconComponent className="w-4 h-4 animate-pulse" />
          </div>

          <div className="space-y-0.5 flex-1">
            <div className="flex items-center space-x-2 flex-wrap">
              {stepContext && (
                <span
                  className={`text-[9px] font-mono font-bold px-2 py-0.5 rounded border tracking-wider uppercase ${scheme.badge}`}
                >
                  {stepContext}
                </span>
              )}
              <h4 className="text-xs font-bold text-slate-100 tracking-tight">{title}</h4>
            </div>

            <p className="text-[11px] text-slate-300 leading-relaxed font-sans">{description}</p>

            {shopFloorTip && (
              <div className="flex items-center space-x-1.5 text-[10px] text-slate-400 pt-0.5 font-mono">
                <Lightbulb className="w-3.5 h-3.5 text-amber-400 flex-shrink-0" />
                <span>{shopFloorTip}</span>
              </div>
            )}
          </div>
        </div>

        {/* Right: 1-Click Remediation Actions */}
        <div className="flex items-center flex-wrap gap-2 lg:self-center">
          {actions.map((act, idx) => {
            const isLoading = loadingIndex === idx;
            const ActionIcon = act.icon;
            const isPrimary = act.variant !== 'secondary';

            return (
              <button
                key={idx}
                type="button"
                onClick={() => handleActionClick(act, idx)}
                disabled={loadingIndex !== null}
                className={`flex items-center space-x-1.5 px-3 py-1.5 rounded text-xs font-bold transition-colors cursor-pointer disabled:opacity-50 ${
                  isPrimary
                    ? scheme.primaryBtn
                    : `${scheme.secondaryBtn} border font-semibold`
                }`}
              >
                {isLoading ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin text-current" />
                ) : ActionIcon ? (
                  <ActionIcon className="w-3.5 h-3.5 flex-shrink-0" />
                ) : null}
                <span>{isLoading && act.loadingText ? act.loadingText : act.label}</span>
              </button>
            );
          })}
        </div>
      </div>
      {error && (
        <p role="alert" className="mt-3 text-xs text-[var(--workspace-danger)] break-words select-text">
          {error}
        </p>
      )}
    </div>
  );
};

export default GuardrailBanner;
