/**
 * src/renderer/components/labeling/CategorySelector.tsx
 * Category tag palette for rapid class assignment with color coding.
 */

import React, { useState } from 'react';
import { Plus, Tag } from 'lucide-react';
import { DEFAULT_CATEGORIES, useAnnotationStore } from '../../stores/useAnnotationStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useProjectStore } from '../../stores/useProjectStore';

export const CategorySelector: React.FC = () => {
  const { categories, activeCategory, setActiveCategory, addCategory, annotations, labelbookVersion } = useAnnotationStore();
  const advanceOnBox = useAnnotationStore((state) => state.advanceOnBox);
  const setAdvanceOnBox = useAnnotationStore((state) => state.setAdvanceOnBox);
  const [newCatName, setNewCatName] = useState('');
  const [isAdding, setIsAdding] = useState(false);
  const [showSamples, setShowSamples] = useState(false);
  // The simple view leads with the project's own classes: the built-in example defect names stay one click away
  // while unused, and a dataset class that has no label class yet is offered as one click. Display only; the
  // categories and their IDs in saved labels are unchanged.
  const simpleView = useProjectStore((state) => state.viewMode) === 'simple';
  // Classification takes each image's class from its folder or the class picker above; this bar names drawn regions.
  const classification = useProjectStore((state) => state.task) === 'classification';
  const detection = useProjectStore((state) => state.task) === 'detection';
  const datasetClasses = Object.keys(useDatasetStore((state) => state.classes) || {});
  const countOf = (cat: { id: number; name: string }) => annotations.filter((a) => a.category_id === cat.id || a.label === cat.name).length;
  const isUnusedSample = (cat: { id: number; name: string }) => simpleView && !labelbookVersion && cat.name !== 'OK'
    && DEFAULT_CATEGORIES.some((d) => d.id === cat.id && d.name === cat.name)
    && !datasetClasses.includes(cat.name) && countOf(cat) === 0;
  // The class used for drawing is always visible, even when it is an unused example name.
  const samples = categories.filter((cat) => isUnusedSample(cat) && cat.id !== activeCategory.id);
  const shown = showSamples ? categories : categories.filter((cat) => !isUnusedSample(cat) || cat.id === activeCategory.id);
  const missing = simpleView && !labelbookVersion ? datasetClasses.filter((name) => !categories.some((cat) => cat.name === name)) : [];

  const handleAdd = () => {
    if (newCatName.trim()) {
      addCategory(newCatName.trim());
      setNewCatName('');
      setIsAdding(false);
    }
  };

  return (
    <div className="bg-slate-950 border-b border-slate-800 px-4 py-2 flex items-center space-x-2 overflow-x-auto select-none">
      {classification && (
        <span className="mr-2 shrink-0 text-[11px] text-slate-500" title="이미지 분류의 정답은 위 '이 이미지의 클래스'에서 정합니다.">
          영역 라벨 (패치 분류·근거용)
        </span>
      )}
      <span className="text-xs font-medium text-slate-400 flex items-center space-x-1 mr-2">
        <Tag className="w-3.5 h-3.5" />
        <span>클래스:</span>
      </span>

      {shown.map((cat) => {
        const count = countOf(cat);
        const isActive = activeCategory.id === cat.id;

        return (
          <button
            key={cat.id}
            aria-pressed={isActive}
            onClick={() => setActiveCategory(cat)}
            className={`flex items-center space-x-1.5 px-2.5 py-1 rounded-full text-xs transition-all cursor-pointer ${
              isActive
                ? 'bg-slate-800 text-white ring-1 ring-blue-400 shadow'
                : 'bg-slate-900 text-slate-400 hover:bg-slate-800 hover:text-slate-200'
            }`}
          >
            <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: cat.color }} />
            <span>{cat.name}</span>
            {count > 0 && (
              <span className="ml-1 px-1.5 py-0.5 rounded-full text-[10px] bg-slate-950 text-slate-300 font-mono">
                {count}
              </span>
            )}
          </button>
        );
      })}

      {missing.map((name) => (
        <button key={`missing:${name}`} type="button" onClick={() => addCategory(name)} title={`데이터셋의 ${name} 클래스를 라벨 클래스로 추가합니다`}
          className="flex items-center gap-1 whitespace-nowrap rounded-full border border-dashed border-cyan-600 px-2.5 py-1 text-xs text-cyan-200 hover:bg-cyan-950/40">
          <Plus className="h-3 w-3" aria-hidden="true" /><span>{name}</span>
        </button>
      ))}
      {samples.length > 0 && (
        <button type="button" aria-expanded={showSamples} onClick={() => setShowSamples((open) => !open)}
          className="whitespace-nowrap rounded-full px-2 py-1 text-xs text-slate-500 hover:text-slate-300">
          {showSamples ? '예시 이름 숨기기' : `예시 이름 ${samples.length}개`}
        </button>
      )}

      {labelbookVersion ? <span className="whitespace-nowrap text-xs text-cyan-300">공유 기준 v{labelbookVersion} · 클래스 추가는 팀 기준서에서</span> : isAdding ? (
        <div className="flex items-center space-x-1">
          <input
            type="text"
            value={newCatName}
            onChange={(e) => setNewCatName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
            placeholder="새 클래스 이름"
            aria-label="새 클래스 이름"
            autoFocus
            className="px-2 py-0.5 text-xs bg-slate-900 border border-slate-700 rounded text-slate-100 w-28 focus:outline-none focus:border-blue-500"
          />
          <button onClick={handleAdd} className="px-2 py-0.5 bg-blue-600 rounded text-xs text-white cursor-pointer">
            추가
          </button>
          <button onClick={() => setIsAdding(false)} aria-label="추가 취소" className="px-1.5 py-0.5 text-xs text-slate-400 hover:text-white cursor-pointer">
            ✕
          </button>
        </div>
      ) : (
        <button
          onClick={() => setIsAdding(true)}
          className="p-1 rounded-full text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors cursor-pointer"
          title="클래스 추가"
          aria-label="클래스 추가"
        >
          <Plus className="w-3.5 h-3.5" />
        </button>
      )}
      {/* 한 장에 하나씩 (detection): one drag per image instead of drag + next; off by default for images with several defects. */}
      {detection && setAdvanceOnBox && <label className="ml-2 flex items-center gap-1 whitespace-nowrap text-xs text-slate-300"
        title="켜면 상자를 하나 그릴 때마다 저장하고 다음 이미지로 넘어갑니다. 한 장에 결함이 여러 개면 끄세요.">
        <input type="checkbox" checked={Boolean(advanceOnBox)} onChange={(event) => setAdvanceOnBox(event.target.checked)} className="accent-cyan-500" />
        그리면 다음 이미지로
      </label>}
    </div>
  );
};
