/**
 * src/renderer/components/common/JargonGlossaryModal.tsx
 * Technical Manual Style Glossary for Manufacturing Vision AI Terminology.
 */

import React, { useState, useMemo, useEffect } from 'react';
import { BookOpen, Search, X, Factory, Lightbulb, Info } from 'lucide-react';
import { useProjectStore } from '../../stores/useProjectStore';
import { JARGON_DICTIONARY, getJargonEntry } from '../../data/jargonDictionary';

export interface JargonGlossaryModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const JargonGlossaryModal: React.FC<JargonGlossaryModalProps> = ({ isOpen, onClose }) => {
  const language=useProjectStore(s=>s.language),isKo=language==='ko';
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('all');

  const entries = useMemo(() => Object.keys(JARGON_DICTIONARY).map(key=>getJargonEntry(key,language)!), [language]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  const filtered = useMemo(() => {
    return entries.filter((e) => {
      const matchCat = selectedCategory === 'all' || e.category === selectedCategory;
      const q = searchTerm.trim().toLowerCase();
      if (!q) return matchCat;
      const matchSearch =
        e.term.toLowerCase().includes(q) ||
        e.termKo.toLowerCase().includes(q) ||
        e.definitionKo.toLowerCase().includes(q) ||
        e.shopFloorMeaningKo.toLowerCase().includes(q) ||
        e.tag.toLowerCase().includes(q);
      return matchCat && matchSearch;
    });
  }, [entries, searchTerm, selectedCategory]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 sm:p-6 select-none animate-in fade-in duration-100"
      onClick={onClose}
    >
      <div
        role="dialog" aria-modal="true" aria-label={isKo?'제조 비전 AI 용어 사전':'Manufacturing vision AI glossary'}
        className="bg-[#131822] border border-[#2B3547] rounded w-full max-w-4xl max-h-[85vh] flex flex-col shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header Strip */}
        <div className="px-5 py-3.5 border-b border-[#2B3547] flex items-center justify-between bg-[#0B0E14]">
          <div className="flex items-center space-x-3">
            <div className="p-1.5 rounded bg-[#1A212E] text-slate-300 border border-[#2B3547]">
              <BookOpen className="w-4 h-4 text-slate-200" />
            </div>
            <div>
              <h2 className="text-xs font-bold text-slate-100 uppercase tracking-wider font-mono flex items-center space-x-2">
                <span>{isKo?'제조 현장 비전 AI 기술 실무 용어 사전':'Manufacturing vision AI glossary'}</span>
                <span className="text-[11px] px-2 py-0.2 rounded bg-[#1A212E] text-slate-300 font-mono tabular-nums border border-[#2B3547]">
                  {entries.length} TERMS
                </span>
              </h2>
              <p className="text-[11px] text-slate-400 mt-0.5">
                {isKo?'복잡한 머신러닝 전문 파라미터를 제조 현장(수율, 결함 유출, 설비 택트타임)의 실무 계측 규격으로 해설합니다.':'Definitions, process implications and checks for vision AI parameters.'}
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded text-slate-400 hover:text-white hover:bg-[#1A212E] border border-transparent hover:border-[#2B3547] transition-colors cursor-pointer"
            title={isKo?'닫기 (Esc)':'Close (Esc)'}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Filter & Search Bar */}
        <div className="p-3.5 border-b border-[#2B3547] flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 bg-[#0E131C]">
          <div className="relative flex-1">
            <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              aria-label={isKo?'용어 검색':'Search glossary'} placeholder={isKo?'용어 검색 (예: Focal Loss, AUROC, 미검, P95, 패딩...)':'Search terms: Focal Loss, AUROC, escape, P95, padding…'}
              className="w-full bg-[#1A212E] border border-[#2B3547] rounded pl-8 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 font-mono focus:outline-none focus:border-slate-400"
            />
          </div>

          <div className="flex space-x-1 text-xs font-semibold overflow-x-auto pb-1 sm:pb-0">
            {[
              { id: 'all', label: isKo?'전체':'All' },
              { id: 'training', label: isKo?'학습':'Training' },
              { id: 'evaluation', label: isKo?'평가/수율':'Evaluation' },
              { id: 'flowchart', label: isKo?'플로우차트':'Flowchart' },
              { id: 'inference', label: isKo?'인라인속도':'Inference' },
            ].map((tab) => {
              const isActive = selectedCategory === tab.id;
              return (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setSelectedCategory(tab.id)}
                  className={`px-3 py-1 rounded text-xs transition-colors shrink-0 font-medium ${
                    isActive
                      ? 'bg-[#2B3547] text-white font-bold border border-[#3B4860]'
                      : 'bg-[#1A212E] text-slate-400 hover:text-slate-200 hover:bg-[#222B3B] border border-[#2B3547]'
                  }`}
                >
                  {tab.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Cards List Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-3 bg-[#131822]">
          {filtered.length === 0 ? (
            <div className="text-center py-12 text-slate-500 text-xs italic font-mono">
              {isKo?'검색 조건에 일치하는 기술 용어가 없습니다.':'No terms match this search.'}
            </div>
          ) : (
            filtered.map((item) => (
              <div
                key={item.term}
                className="bg-[#1A212E] rounded border border-[#2B3547] p-3.5 space-y-2.5 hover:border-slate-500 transition-colors"
              >
                <div className="flex items-center justify-between pb-2 border-b border-[#2B3547]">
                  <div className="flex items-center space-x-2">
                    <span className="text-xs font-bold text-slate-100">{item.termKo}</span>
                    <span className="font-mono text-[11px] text-slate-300 bg-[#131822] px-2 py-0.5 rounded border border-[#2B3547]">
                      {item.term}
                    </span>
                  </div>
                  <span className="text-[10px] text-slate-400 uppercase font-mono tracking-wider">
                    {item.categoryLabelKo}
                  </span>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                  <div className="space-y-1">
                    <div className="flex items-center space-x-1.5 font-bold text-slate-300 text-[11px] uppercase tracking-wide">
                      <Info className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                      <span>{isKo?'원리 및 기능 정의':'Definition'}</span>
                    </div>
                    <p className="text-slate-300 text-[11px] leading-relaxed pl-5 font-sans">
                      {item.definitionKo}
                    </p>
                  </div>

                  <div className="space-y-1">
                    <div className="flex items-center space-x-1.5 font-bold text-amber-400 text-[11px] uppercase tracking-wide">
                      <Factory className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                      <span>{isKo?'제조 현장 실무 관점에서의 의미':'Process implications'}</span>
                    </div>
                    <p className="text-slate-300 text-[11px] leading-relaxed pl-5 font-sans">
                      {item.shopFloorMeaningKo}
                    </p>
                  </div>
                </div>

                <div className="p-2 bg-[#131822] rounded border border-[#2B3547] flex items-center space-x-2 text-[11px]">
                  <Lightbulb className="w-3.5 h-3.5 text-[#10B981] shrink-0" />
                  <span className="font-bold text-[#10B981] shrink-0 font-mono text-[10px] uppercase">
                    {isKo?'[권장 기준]:':'[Checks]:'}
                  </span>
                  <span className="text-slate-200 font-mono text-[11px]">{item.recommendedValueKo}</span>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
};

export default JargonGlossaryModal;
