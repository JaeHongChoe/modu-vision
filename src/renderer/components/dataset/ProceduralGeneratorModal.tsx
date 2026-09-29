/**
 * src/renderer/components/dataset/ProceduralGeneratorModal.tsx
 * Modal for generating synthetic defect datasets (PCB, Wafer, Metal flaws).
 */

import React, { useState } from 'react';
import { Sparkles, X, AlertCircle } from 'lucide-react';
import { useProjectStore } from '../../stores/useProjectStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import type { VisionTask } from '../../types';

export const ProceduralGeneratorModal: React.FC = () => {
  const { task, language } = useProjectStore();
  const { showGeneratorModal, setShowGeneratorModal, generateSynthetic, isGenerating } = useDatasetStore();

  const [modality, setModality] = useState<'pcb' | 'wafer' | 'metal'>('pcb');
  const [numSamples, setNumSamples] = useState<number>(100);
  const [splitRatio, setSplitRatio] = useState<number>(0.8);
  const [genTask, setGenTask] = useState<VisionTask>(task);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  if (!showGeneratorModal) return null;

  const handleGenerate = async () => {
    setErrorMsg(null);
    try {
      await generateSynthetic({
        task: genTask,
        num_samples: numSamples,
        modality,
        split_ratio: splitRatio,
      });
    } catch (err: any) {
      setErrorMsg(err.message || String(err));
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-[#0B0E14]/85 flex items-center justify-center p-4">
      <div className="bg-[#131822] border border-[#2B3547] rounded-[6px] max-w-lg w-full p-5 text-slate-200">
        <div className="flex items-center justify-between pb-3.5 border-b border-[#2B3547]">
          <div className="flex items-center space-x-2">
            <Sparkles className="w-5 h-5 text-blue-400" />
            <h3 className="font-bold text-base">
              {language === 'ko' ? '절차적 합성 결함 데이터 생성기' : 'Procedural Industrial Defect Generator'}
            </h3>
          </div>
          <button
            onClick={() => setShowGeneratorModal(false)}
            disabled={isGenerating}
            className="text-slate-400 hover:text-white p-1 rounded hover:bg-[#1A212E] transition-tactile cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {errorMsg && (
          <div className="mt-4 p-3 bg-[#EF4444]/10 border border-[#EF4444]/40 rounded-[4px] flex items-center space-x-2 text-xs text-red-200">
            <AlertCircle className="w-4 h-4 text-[#EF4444] shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}

        <div className="space-y-4 py-4 text-xs">
          {/* Modality Selection */}
          <div>
            <label className="block font-medium text-slate-400 mb-1">
              {language === 'ko' ? '제조 검사 도메인 (Modality)' : 'Defect Modality'}
            </label>
            <div className="grid grid-cols-3 gap-2">
              {[
                { id: 'pcb', nameKo: 'PCB 기판 결함', descKo: '단락, 납볼, 회로단선' },
                { id: 'wafer', nameKo: '반도체 웨이퍼', descKo: '동심원 스크래치, 링' },
                { id: 'metal', nameKo: '금속 표면 스크래치', descKo: '헤어라인 크랙, 찍힘' },
              ].map((m) => (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => setModality(m.id as any)}
                  className={`p-2.5 rounded-[4px] border text-left transition-tactile cursor-pointer ${
                    modality === m.id
                      ? 'border-[#3B82F6] bg-[#1E293B] text-blue-300 font-semibold ring-1 ring-[#3B82F6]'
                      : 'border-[#2B3547] bg-[#1A212E] text-slate-300 hover:bg-[#222B3D]'
                  }`}
                >
                  <div className="text-xs">{m.nameKo}</div>
                  <div className="text-[10px] text-slate-400 mt-1">{m.descKo}</div>
                </button>
              ))}
            </div>
          </div>

          {/* Vision Task Selection */}
          <div>
            <label className="block font-medium text-slate-400 mb-1">
              {language === 'ko' ? '비전 태스크 라벨 형식' : 'Vision Task Target'}
            </label>
            <div className="grid grid-cols-2 gap-2">
              {[
                { id: 'classification', label: 'Classification (OK/NG)' },
                { id: 'detection', label: 'Detection (BBox Flaws)' },
                { id: 'segmentation', label: 'Segmentation (Pixel Mask)' },
                { id: 'anomaly', label: 'Anomaly (Normal-only OK)' },
              ].map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setGenTask(t.id as any)}
                  className={`p-2 rounded-[4px] border text-left text-xs transition-tactile cursor-pointer ${
                    genTask === t.id
                      ? 'border-[#3B82F6] bg-[#1E293B] text-blue-300 font-semibold ring-1 ring-[#3B82F6]'
                      : 'border-[#2B3547] bg-[#1A212E] text-slate-300 hover:bg-[#222B3D]'
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>

          {/* Sample Count Selection */}
          <div>
            <label className="block font-medium text-slate-400 mb-1">
              {language === 'ko' ? `생성 샘플 수량: ${numSamples}장` : `Sample Count: ${numSamples} images`}
            </label>
            <div className="flex space-x-2">
              {[50, 100, 200, 500].map((count) => (
                <button
                  key={count}
                  type="button"
                  onClick={() => setNumSamples(count)}
                  className={`flex-1 py-1.5 rounded-[4px] border text-xs font-mono tabular-nums font-medium transition-tactile cursor-pointer ${
                    numSamples === count
                      ? 'border-blue-400 bg-blue-600 text-white font-bold'
                      : 'border-[#2B3547] bg-[#1A212E] text-slate-300 hover:bg-[#222B3D]'
                  }`}
                >
                  {count}
                </button>
              ))}
            </div>
          </div>

          {/* Split Ratio Slider */}
          <div>
            <div className="flex justify-between text-slate-400 mb-1">
              <span>{language === 'ko' ? '학습 / 검증 분할 비율' : 'Train / Val Split'}</span>
              <span className="font-mono tabular-nums text-blue-400">
                {Math.round(splitRatio * 100)}% Train / {Math.round((1 - splitRatio) * 100)}% Val
              </span>
            </div>
            <input
              type="range"
              min="0.5"
              max="0.9"
              step="0.05"
              value={splitRatio}
              onChange={(e) => setSplitRatio(parseFloat(e.target.value))}
              className="w-full accent-blue-500 bg-[#0B0E14] h-1.5 rounded cursor-pointer"
            />
          </div>
        </div>

        <div className="flex justify-end space-x-3 pt-4 border-t border-[#2B3547]">
          <button
            type="button"
            onClick={() => setShowGeneratorModal(false)}
            disabled={isGenerating}
            className="px-4 py-1.5 rounded-[4px] bg-[#1A212E] hover:bg-[#222B3D] text-xs font-medium text-slate-300 border border-[#2B3547] transition-tactile cursor-pointer"
          >
            {language === 'ko' ? '취소' : 'Cancel'}
          </button>

          <button
            type="button"
            onClick={handleGenerate}
            disabled={isGenerating}
            className="flex items-center space-x-2 px-5 py-1.5 rounded-[4px] bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold border border-blue-400 transition-tactile cursor-pointer disabled:opacity-50"
          >
            <Sparkles className="w-3.5 h-3.5" />
            <span>
              {isGenerating
                ? language === 'ko'
                  ? '합성 렌더링 중...'
                  : 'Generating...'
                : language === 'ko'
                ? '합성 데이터셋 생성 시작'
                : 'Generate Dataset'}
            </span>
          </button>
        </div>
      </div>
    </div>
  );
};
