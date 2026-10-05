/**
 * src/renderer/components/common/JargonTooltip.tsx
 * Accessible, interactive popover tooltip for manufacturing ML jargon.
 * Uses a dark steel chassis theme with industrial interface controls:
 * - Solid #131822 chassis panel (Zero blurs, zero cyan glow)
 * - 1px hairline precision borders (#2B3547)
 * - High-contrast text throughout (WCAG AAA compliant)
 * - Keyboard (Escape) & click-outside dismissible
 */

import React, { useState, useRef, useEffect } from 'react';
import { HelpCircle, Lightbulb, Factory, Info, X } from 'lucide-react';
import { useProjectStore } from '../../stores/useProjectStore';
import { getJargonEntry } from '../../data/jargonDictionary';

export interface JargonTooltipProps {
  termKey: string;
  children?: React.ReactNode;
  className?: string;
  align?: 'left' | 'center' | 'right';
  side?: 'top' | 'bottom';
}

export const JargonTooltip: React.FC<JargonTooltipProps> = ({
  termKey,
  children,
  className = '',
  align = 'center',
  side = 'bottom',
}) => {
  const language=useProjectStore(s=>s.language),isKo=language==='ko';
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const entry = getJargonEntry(termKey,language);

  // Close on outside click or Escape key
  useEffect(() => {
    if (!isOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen]);

  if (!entry) {
    return <span className={className}>{children || termKey}</span>;
  }

  const alignClass =
    align === 'left' ? 'left-0' : align === 'right' ? 'right-0' : 'left-1/2 -translate-x-1/2';

  const sideClass =
    side === 'top' ? 'bottom-full mb-2' : 'top-full mt-2';

  return (
    <div
      ref={containerRef}
      className={`relative inline-flex items-center ${className}`}
      onMouseEnter={() => setIsOpen(true)}
      onMouseLeave={() => setIsOpen(false)}
    >
      {/* Trigger: Industrial Dotted Underline */}
      {children ? (
        <span
          onClick={(e) => {
            e.stopPropagation();
            setIsOpen((prev) => !prev);
          }}
          className="cursor-help inline-flex items-center space-x-1 border-b border-dotted border-slate-500 hover:border-slate-300 text-slate-300 hover:text-slate-100 transition-colors"
        >
          <span>{children}</span>
          <HelpCircle className="w-3 h-3 text-slate-400 inline shrink-0" />
        </span>
      ) : (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setIsOpen((prev) => !prev);
          }}
          aria-label={`${isKo?'용어 설명':'Term explanation'}: ${entry.termKo}`}
          className="p-0.5 rounded text-slate-400 hover:text-slate-200 hover:bg-[#1A212E] transition-colors cursor-help inline-flex items-center justify-center border border-transparent hover:border-[#2B3547]"
        >
          <HelpCircle className="w-3.5 h-3.5" />
        </button>
      )}

      {/* Popover Card: Solid Dark Steel Enclosure */}
      {isOpen && (
        <div
          role="tooltip"
          onClick={(e) => e.stopPropagation()}
          className={`absolute ${sideClass} ${alignClass} w-80 sm:w-96 bg-[#131822] border border-[#2B3547] rounded shadow-2xl shadow-black/80 p-4 text-xs text-slate-200 z-50 animate-in fade-in zoom-in-95 duration-100 select-text`}
          style={{ minWidth: '19rem', maxWidth: '90vw' }}
        >
          {/* Header Bar */}
          <div className="flex items-start justify-between pb-2.5 mb-2.5 border-b border-[#2B3547] bg-[#0B0E14] -mx-4 -mt-4 p-3 rounded-t">
            <div>
              <div className="flex items-center space-x-2">
                <span className="font-bold text-slate-100 text-sm tracking-wide font-mono">
                  {entry.termKo}
                </span>
                <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-[#1A212E] text-slate-200 border border-[#2B3547]">
                  {entry.term}
                </span>
              </div>
              <div className="flex items-center space-x-2 mt-1">
                <span className="text-[10px] text-slate-400 font-semibold uppercase tracking-wider font-mono">
                  {isKo?'분류':'Category'}: {entry.categoryLabelKo}
                </span>
                <span className="text-slate-400 font-bold">•</span>
                <span className="text-[10px] text-emerald-400 font-mono">
                  #{entry.tag}
                </span>
              </div>
            </div>

            <button
              type="button"
              onClick={() => setIsOpen(false)}
              className="text-slate-400 hover:text-white p-1 rounded hover:bg-[#1A212E] border border-transparent hover:border-[#2B3547] transition-colors cursor-pointer"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>

          {/* 3 Pillars Content */}
          <div className="space-y-3 leading-relaxed">
            {/* 1. 이게 무엇인가요? */}
            <div className="space-y-1">
              <div className="flex items-center space-x-1.5 font-bold text-blue-400 text-[11px] uppercase tracking-wide">
                <Info className="w-3.5 h-3.5 text-blue-400 shrink-0" />
                <span>{isKo?'이게 무엇인가요?':'What is it?'}</span>
              </div>
              <p className="text-slate-300 text-[11px] pl-5 font-sans leading-relaxed">
                {entry.definitionKo}
              </p>
            </div>

            {/* 2. 제조 현장 실무 관점에서의 의미 */}
            <div className="space-y-1">
              <div className="flex items-center space-x-1.5 font-bold text-amber-400 text-[11px] uppercase tracking-wide">
                <Factory className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                <span>{isKo?'제조 현장 실무 관점에서의 의미':'Process implications'}</span>
              </div>
              <p className="text-slate-300 text-[11px] pl-5 font-sans leading-relaxed">
                {entry.shopFloorMeaningKo}
              </p>
            </div>

            {/* 3. 권장 기준 & 조치 가이드 */}
            <div className="p-2.5 bg-[#0E2018] rounded border border-[#10B981]/50 space-y-1">
              <div className="flex items-center space-x-1.5 font-bold text-[#10B981] text-[11px] uppercase tracking-wide">
                <Lightbulb className="w-3.5 h-3.5 text-[#10B981] shrink-0" />
                <span>{isKo?'권장 기준 & 조치 가이드':'Checks and guidance'}</span>
              </div>
              <p className="text-emerald-100 text-[11px] pl-5 font-medium leading-relaxed font-sans">
                {entry.recommendedValueKo}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default JargonTooltip;
