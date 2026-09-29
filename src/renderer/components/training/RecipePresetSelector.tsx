/**
 * src/renderer/components/training/RecipePresetSelector.tsx
 * Industrial Recipe Presets Selector & Inspection Parameter Matrix Table.
 * Features factory line recipe cards ("⚡ Fast Prototype" vs "🎯 High Precision")
 * and detailed side-by-side comparison matrix with strict tabular-nums.
 */

import React, { useState } from 'react';
import { Sliders, Table, CheckCircle2, ChevronDown, ChevronUp } from 'lucide-react';
import type { TrainingPreset } from '../../types';
import { JargonTooltip } from '../common/JargonTooltip';

export interface RecipePresetSelectorProps {
  preset: TrainingPreset;
  setPreset: (preset: TrainingPreset) => void;
  isTraining: boolean;
  language: 'ko' | 'en';
}

export const RecipePresetSelector: React.FC<RecipePresetSelectorProps> = ({
  preset,
  setPreset,
  isTraining,
  language,
}) => {
  const [showMatrix, setShowMatrix] = useState<boolean>(false);

  return (
    <div className="space-y-3 select-none">
      {/* Header and Comparison Toggle */}
      <div className="flex items-center justify-between">
        <h2 className="text-xs font-bold text-slate-200 tracking-wider uppercase flex items-center space-x-2">
          <Sliders className="w-3.5 h-3.5 text-blue-400" />
          <span>{language === 'ko' ? '검사 공정 레시피 선택 (AutoML Strategy)' : 'Inspection Recipe Preset'}</span>
        </h2>

        <button
          type="button"
          onClick={() => setShowMatrix(!showMatrix)}
          className="inline-flex items-center space-x-1.5 px-2.5 py-1 rounded-[3px] bg-[#131822] hover:bg-[#1A212E] border border-[#2B3547] text-[10px] font-mono text-slate-300 transition-all cursor-pointer"
        >
          <Table className="w-3 h-3 text-slate-400" />
          <span>
            {showMatrix
              ? language === 'ko'
                ? '카드 보기'
                : 'Show Cards'
              : language === 'ko'
              ? '상세 파라미터 매트릭스'
              : 'Parameter Matrix'}
          </span>
          {showMatrix ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        </button>
      </div>

      {/* Recipe Cards View */}
      <div className="grid grid-cols-2 gap-4">
        {/* Recipe A: Fast Prototype Card */}
        <div
          onClick={() => !isTraining && setPreset('fast')}
          className={`p-4 rounded-[4px] border transition-all cursor-pointer relative ${
            preset === 'fast'
              ? 'border-[#3B82F6] bg-[#1A212E] ring-1 ring-[#3B82F6]'
              : 'border-[#2B3547] bg-[#131822] hover:bg-[#1A212E] hover:border-[#475569] text-slate-400'
          } ${isTraining ? 'cursor-not-allowed opacity-80' : ''}`}
        >
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center space-x-2">
              <span className="w-2 h-2 rounded-full bg-amber-400" />
              <span className="font-bold text-xs text-slate-100 uppercase tracking-wide">
                {language === 'ko' ? '⚡ 빠른 프로토타입 (Recipe A)' : '⚡ Fast Prototype (Recipe A)'}
              </span>
            </div>
            <span className="text-[10px] font-mono font-semibold px-2 py-0.5 rounded-[2px] bg-[#0B0E14] border border-[#2B3547] text-blue-300 tabular-nums">
              5 Epochs • 1-2 min
            </span>
          </div>

          <p className="text-[11px] text-slate-400 mb-3 leading-relaxed">
            {language === 'ko'
              ? '신속한 결함 검출 가능성 타진을 위한 고속 수렴 레시피. 가벼운 데이터 증강 및 빠른 웜업 학습률을 적용합니다.'
              : 'Rapid defect feasibility screening preset with light augmentation and fast warmup learning rate.'}
          </p>

          <div className="flex flex-wrap gap-1.5 text-[10px] font-mono text-slate-300">
            <span className="bg-[#0B0E14] px-2 py-0.5 rounded-[2px] border border-[#2B3547] tabular-nums">
              <JargonTooltip termKey="learning_rate">LR: 1.0e-3</JargonTooltip>
            </span>
            <span className="bg-[#0B0E14] px-2 py-0.5 rounded-[2px] border border-[#2B3547] tabular-nums">
              <JargonTooltip termKey="batch_size">Batch: 16</JargonTooltip>
            </span>
            <span className="bg-[#0B0E14] px-2 py-0.5 rounded-[2px] border border-[#2B3547]">Light Aug</span>
            <span className="bg-[#0B0E14] px-2 py-0.5 rounded-[2px] border border-[#2B3547]">Inline Feasibility</span>
          </div>

          {preset === 'fast' && (
            <div className="absolute top-2 right-2 text-blue-400">
              <CheckCircle2 className="w-4 h-4" />
            </div>
          )}
        </div>

        {/* Recipe B: High Precision Card */}
        <div
          onClick={() => !isTraining && setPreset('precision')}
          className={`p-4 rounded-[4px] border transition-all cursor-pointer relative ${
            preset === 'precision'
              ? 'border-[#3B82F6] bg-[#1A212E] ring-1 ring-[#3B82F6]'
              : 'border-[#2B3547] bg-[#131822] hover:bg-[#1A212E] hover:border-[#475569] text-slate-400'
          } ${isTraining ? 'cursor-not-allowed opacity-80' : ''}`}
        >
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center space-x-2">
              <span className="w-2 h-2 rounded-full bg-emerald-400" />
              <span className="font-bold text-xs text-slate-100 uppercase tracking-wide">
                {language === 'ko' ? '🎯 고정밀 프로덕션 (Recipe B)' : '🎯 High Precision (Recipe B)'}
              </span>
            </div>
            <span className="text-[10px] font-mono font-semibold px-2 py-0.5 rounded-[2px] bg-[#0B0E14] border border-[#2B3547] text-emerald-300 tabular-nums">
              20 Epochs • Early Stop
            </span>
          </div>

          <p className="text-[11px] text-slate-400 mb-3 leading-relaxed">
            {language === 'ko'
              ? '양산 라인 배포를 위한 최고 정밀도 레시피. 코사인 학습률 감쇄 및 산업용 고강도 데이터 증강을 적용합니다.'
              : 'Production-grade precision training with cosine annealing learning rate schedule and heavy industrial augmentations.'}
          </p>

          <div className="flex flex-wrap gap-1.5 text-[10px] font-mono text-slate-300">
            <span className="bg-[#0B0E14] px-2 py-0.5 rounded-[2px] border border-[#2B3547] tabular-nums">
              <JargonTooltip termKey="learning_rate">LR: 5.0e-4 Cosine</JargonTooltip>
            </span>
            <span className="bg-[#0B0E14] px-2 py-0.5 rounded-[2px] border border-[#2B3547] tabular-nums">Heavy Aug</span>
            <span className="bg-[#0B0E14] px-2 py-0.5 rounded-[2px] border border-[#2B3547] tabular-nums">
              <JargonTooltip termKey="early_stopping">Patience: 5</JargonTooltip>
            </span>
            <span className="bg-[#0B0E14] px-2 py-0.5 rounded-[2px] border border-[#2B3547]">Zero Escape</span>
          </div>

          {preset === 'precision' && (
            <div className="absolute top-2 right-2 text-blue-400">
              <CheckCircle2 className="w-4 h-4" />
            </div>
          )}
        </div>
      </div>

      {/* Collapsible 8-Parameter Side-by-Side Comparison Matrix Table */}
      {showMatrix && (
        <div className="bg-[#0B0E14] border border-[#2B3547] rounded-[4px] p-3 overflow-x-auto">
          <table className="w-full text-left text-[11px] font-mono border-collapse">
            <thead>
              <tr className="border-b border-[#2B3547] text-slate-400 uppercase text-[10px]">
                <th className="py-1.5 px-3">INSPECTION PARAMETER</th>
                <th className="py-1.5 px-3 text-blue-400">⚡ FAST PROTOTYPE (RECIPE A)</th>
                <th className="py-1.5 px-3 text-emerald-400">🎯 HIGH PRECISION (RECIPE B)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1E293B] text-slate-300">
              <tr>
                <td className="py-1.5 px-3 font-semibold text-slate-400">Target Line Process (적합 공정)</td>
                <td className="py-1.5 px-3">고속 인라인 스크리닝 / 불량 타진</td>
                <td className="py-1.5 px-3">양산 출하 전수검사 (Zero Escape)</td>
              </tr>
              <tr>
                <td className="py-1.5 px-3 font-semibold text-slate-400">Max Epochs & Patience (학습 주기)</td>
                <td className="py-1.5 px-3 tabular-nums">5 Epochs (Fixed)</td>
                <td className="py-1.5 px-3 tabular-nums">20 Epochs (Early Stopping, Patience=5)</td>
              </tr>
              <tr>
                <td className="py-1.5 px-3 font-semibold text-slate-400">Learning Rate Policy (학습률 정책)</td>
                <td className="py-1.5 px-3 tabular-nums">1.0e-3 (Linear Warmup)</td>
                <td className="py-1.5 px-3 tabular-nums">5.0e-4 (Cosine Annealing Decay)</td>
              </tr>
              <tr>
                <td className="py-1.5 px-3 font-semibold text-slate-400">Batch Size & Accumulation (배치 규격)</td>
                <td className="py-1.5 px-3 tabular-nums">16 (AdamW, Weight Decay 1e-4)</td>
                <td className="py-1.5 px-3 tabular-nums">16 + Grad Accum (Weight Decay 1e-2)</td>
              </tr>
              <tr>
                <td className="py-1.5 px-3 font-semibold text-slate-400">Data Augmentation (데이터 증강)</td>
                <td className="py-1.5 px-3">Light (Flip, ±5° Rotate)</td>
                <td className="py-1.5 px-3">Heavy (Lighting Jitter, Cutout, ±15° OBB)</td>
              </tr>
              <tr>
                <td className="py-1.5 px-3 font-semibold text-slate-400">Defect Resolution Limit (검출 한계)</td>
                <td className="py-1.5 px-3 tabular-nums">≥ 15 px (일반 결함)</td>
                <td className="py-1.5 px-3 tabular-nums">≥ 2 px (초미세 크랙/핀홀/이물)</td>
              </tr>
              <tr>
                <td className="py-1.5 px-3 font-semibold text-slate-400">Tact Time Estimate (소요 시간)</td>
                <td className="py-1.5 px-3 tabular-nums">1 ~ 2 분 (Apple Silicon / CUDA)</td>
                <td className="py-1.5 px-3 tabular-nums">5 ~ 15 분 (수렴 보장)</td>
              </tr>
              <tr>
                <td className="py-1.5 px-3 font-semibold text-slate-400">Inspection Standard (검사 규격)</td>
                <td className="py-1.5 px-3">타입 B 프로토타입 기준</td>
                <td className="py-1.5 px-3 font-bold text-emerald-400">타입 C 상용 머신비전 무결점</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
