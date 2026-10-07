/**
 * src/renderer/components/labeling/CategorySelector.tsx
 * Category tag palette for rapid class assignment with color coding.
 */

import React, { useState } from 'react';
import { Plus, Tag } from 'lucide-react';
import { useAnnotationStore } from '../../stores/useAnnotationStore';

export const CategorySelector: React.FC = () => {
  const { categories, activeCategory, setActiveCategory, addCategory, annotations, labelbookVersion } = useAnnotationStore();
  const [newCatName, setNewCatName] = useState('');
  const [isAdding, setIsAdding] = useState(false);

  const handleAdd = () => {
    if (newCatName.trim()) {
      addCategory(newCatName.trim());
      setNewCatName('');
      setIsAdding(false);
    }
  };

  return (
    <div className="bg-slate-950 border-b border-slate-800 px-4 py-2 flex items-center space-x-2 overflow-x-auto select-none">
      <span className="text-xs font-medium text-slate-400 flex items-center space-x-1 mr-2">
        <Tag className="w-3.5 h-3.5" />
        <span>Classes:</span>
      </span>

      {categories.map((cat) => {
        const count = annotations.filter((a) => a.category_id === cat.id || a.label === cat.name).length;
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

      {labelbookVersion ? <span className="whitespace-nowrap text-xs text-cyan-300">공유 기준 v{labelbookVersion} · 클래스 추가는 팀 기준서에서</span> : isAdding ? (
        <div className="flex items-center space-x-1">
          <input
            type="text"
            value={newCatName}
            onChange={(e) => setNewCatName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
            placeholder="New class..."
            autoFocus
            className="px-2 py-0.5 text-xs bg-slate-900 border border-slate-700 rounded text-slate-100 w-28 focus:outline-none focus:border-blue-500"
          />
          <button onClick={handleAdd} className="px-2 py-0.5 bg-blue-600 rounded text-xs text-white cursor-pointer">
            Add
          </button>
          <button onClick={() => setIsAdding(false)} className="px-1.5 py-0.5 text-xs text-slate-400 hover:text-white cursor-pointer">
            ✕
          </button>
        </div>
      ) : (
        <button
          onClick={() => setIsAdding(true)}
          className="p-1 rounded-full text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors cursor-pointer"
          title="Add new category"
        >
          <Plus className="w-3.5 h-3.5" />
        </button>
      )}
    </div>
  );
};
