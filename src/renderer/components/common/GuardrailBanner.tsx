/**
 * src/renderer/components/common/GuardrailBanner.tsx
 * In-Page Inline Guardrail UI & 1-Click Remediation Component (Feature F32).
 * Renders contextual non-intimidating banners when essential workflow inputs
 * or prerequisites are missing, providing prominent 1-click remediation actions.
 */

import React, { useState } from 'react';
import {
  AlertTriangle,
  Info,
  ShieldAlert,
  CheckCircle2,
  Loader2,
  LucideIcon,
  Lightbulb,
} from 'lucide-react';

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
  const [loadingIndex, setLoadingIndex] = useState<number | null>(null);

  const handleActionClick = async (action: GuardrailAction, index: number) => {
    if (loadingIndex !== null) return;
    try {
      setLoadingIndex(index);
      await action.onClick();
    } catch (err) {
      console.error('Guardrail remediation action failed:', err);
    } finally {
      setLoadingIndex(null);
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
    </div>
  );
};

export default GuardrailBanner;
