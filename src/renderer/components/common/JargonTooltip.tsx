/** Rich term help. Radix owns keyboard focus, dismissal and viewport collision handling. */
import React, { useState, useRef, useId } from 'react';
import * as Popover from '@radix-ui/react-popover';
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
  const containerRef = useRef<HTMLSpanElement>(null);
  const [portalContainer, setPortalContainer] = useState<HTMLElement | undefined>();
  const titleId = useId();
  const entry = getJargonEntry(termKey,language);
  if (!entry) return <span className={className}>{children || termKey}</span>;
  const changeOpen = (open:boolean) => {
    // A help popup opened inside a workspace modal belongs to that modal's focus scope.
    if (open) setPortalContainer(containerRef.current?.closest<HTMLElement>('[role="dialog"]') || undefined);
    setIsOpen(open);
  };

  return (
    <span ref={containerRef} className={`inline-flex items-center ${className}`}>
      <Popover.Root open={isOpen} onOpenChange={changeOpen}>
        <Popover.Trigger asChild>
          <button type="button"
            aria-label={`${isKo?'용어 설명':'Term explanation'}: ${entry.termKo}`}
            onClick={event => event.stopPropagation()}
            className={children
              ? 'cursor-help inline-flex items-center gap-1 border-b border-dotted border-slate-500 hover:border-slate-300 text-slate-300 hover:text-slate-100 transition-colors'
              : 'p-0.5 rounded text-slate-400 hover:text-slate-200 hover:bg-[#1A212E] transition-colors cursor-help inline-flex items-center justify-center border border-transparent hover:border-[#2B3547]'}>
            {children && <span>{children}</span>}
            <HelpCircle aria-hidden="true" className="w-3.5 h-3.5 shrink-0" />
          </button>
        </Popover.Trigger>
        <Popover.Portal container={portalContainer}>
          <Popover.Content data-jargon-popover="" side={side} align={align === 'left' ? 'start' : align === 'right' ? 'end' : 'center'}
            sideOffset={8} collisionPadding={12} aria-labelledby={titleId}
            onEscapeKeyDown={event => event.stopPropagation()}
            onClick={event => event.stopPropagation()}
            className="w-80 sm:w-96 bg-[#131822] border border-[#2B3547] rounded shadow-2xl shadow-black/80 p-4 text-xs text-slate-200 select-text overflow-y-auto"
            style={{zIndex:160,maxWidth:'calc(100vw - 24px)',maxHeight:'var(--radix-popover-content-available-height)'}}>
          {/* Header Bar */}
          <div className="flex items-start justify-between pb-2.5 mb-2.5 border-b border-[#2B3547] bg-[#0B0E14] -mx-4 -mt-4 p-3 rounded-t">
            <div>
              <div id={titleId} aria-label={`${entry.termKo} ${isKo?'용어 설명':'term explanation'}`} className="flex items-center space-x-2">
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

            <Popover.Close
              type="button"
              aria-label={isKo?'용어 설명 닫기':'Close term explanation'}
              className="text-slate-400 hover:text-white p-1 rounded hover:bg-[#1A212E] border border-transparent hover:border-[#2B3547] transition-colors cursor-pointer"
            >
              <X aria-hidden="true" className="w-3.5 h-3.5" />
            </Popover.Close>
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
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
    </span>
  );
};

export default JargonTooltip;
