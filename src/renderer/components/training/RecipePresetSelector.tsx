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
          <span>{language === 'ko' ? '학습 방식 선택' : 'Training style'}</span>
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
          role="button"
          tabIndex={isTraining ? -1 : 0}
          onClick={() => !isTraining && setPreset('fast')}
          onKeyDown={(event) => {
            if (!isTraining && (event.key === 'Enter' || event.key === ' ')) {
              event.preventDefault();
              setPreset('fast');
            }
          }}
          aria-disabled={isTraining}
          aria-pressed={preset === 'fast'}
          className={`p-4 rounded-[4px] border transition-all cursor-pointer relative text-left ${
            preset === 'fast'
              ? 'border-[#3B82F6] bg-[#1A212E] ring-1 ring-[#3B82F6]'
              : 'border-[#2B3547] bg-[#131822] hover:bg-[#1A212E] hover:border-[#475569] text-slate-400'
          } ${isTraining ? 'cursor-not-allowed opacity-80' : ''}`}
        >
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center space-x-2">
              <span className="w-2 h-2 rounded-full bg-amber-400" />
              <span className="font-bold text-xs text-slate-100 uppercase tracking-wide">
                {language === 'ko' ? '빠른 시험 학습 (A)' : 'Quick trial (A)'}
              </span>
            </div>
            <span className="text-[10px] font-mono font-semibold px-2 py-0.5 rounded-[2px] bg-[#0B0E14] border border-[#2B3547] text-blue-300 tabular-nums">
              최대 12회 학습 · 개선이 없으면 자동 종료
            </span>
          </div>

          <p className="text-[11px] text-slate-400 mb-3 leading-relaxed">
            {language === 'ko'
              ? '신속한 결함 검출 가능성 타진을 위한 고속 수렴 레시피. 가벼운 데이터 증강 및 빠른 웜업 학습률을 적용합니다.'
              : 'Rapid defect feasibility screening preset with light augmentation and fast warmup learning rate.'}
          </p>

          <div className="flex flex-wrap gap-1.5 text-[10px] font-mono text-slate-300">
            <span className="bg-[#0B0E14] px-2 py-0.5 rounded-[2px] border border-[#2B3547] tabular-nums">
              <JargonTooltip termKey="learning_rate">학습률 0.001</JargonTooltip>
            </span>
            <span className="bg-[#0B0E14] px-2 py-0.5 rounded-[2px] border border-[#2B3547] tabular-nums">
              <JargonTooltip termKey="batch_size">한 번에 16장</JargonTooltip>
            </span>
            <span className="bg-[#0B0E14] px-2 py-0.5 rounded-[2px] border border-[#2B3547]">가벼운 이미지 변형</span>
            <span className="bg-[#0B0E14] px-2 py-0.5 rounded-[2px] border border-[#2B3547]">가능성 확인용</span>
          </div>

          {preset === 'fast' && (
            <div className="absolute top-2 right-2 text-blue-400">
              <CheckCircle2 className="w-4 h-4" />
            </div>
          )}
        </div>

        {/* Recipe B: High Precision Card */}
        <div
          role="button"
          tabIndex={isTraining ? -1 : 0}
          onClick={() => !isTraining && setPreset('precision')}
          onKeyDown={(event) => {
            if (!isTraining && (event.key === 'Enter' || event.key === ' ')) {
              event.preventDefault();
              setPreset('precision');
            }
          }}
          aria-disabled={isTraining}
          aria-pressed={preset === 'precision'}
          className={`p-4 rounded-[4px] border transition-all cursor-pointer relative text-left ${
            preset === 'precision'
              ? 'border-[#3B82F6] bg-[#1A212E] ring-1 ring-[#3B82F6]'
              : 'border-[#2B3547] bg-[#131822] hover:bg-[#1A212E] hover:border-[#475569] text-slate-400'
          } ${isTraining ? 'cursor-not-allowed opacity-80' : ''}`}
        >
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center space-x-2">
              <span className="w-2 h-2 rounded-full bg-emerald-400" aria-hidden="true" />
              <span className="font-bold text-xs text-slate-100 uppercase tracking-wide">
                {language === 'ko' ? '정밀 후보 학습 (B)' : 'Precision candidate (B)'}
              </span>
            </div>
            <span className="text-[10px] font-mono font-semibold px-2 py-0.5 rounded-[2px] bg-[#0B0E14] border border-[#2B3547] text-emerald-300 tabular-nums">
              최대 30회 학습 · 개선이 없으면 자동 종료
            </span>
          </div>

          <p className="text-[11px] text-slate-400 mb-3 leading-relaxed">
            {language === 'ko'
              ? '더 큰 입력 이미지와 강한 증강으로 후보를 학습합니다. 양산 적용 여부는 별도 시험 데이터와 현장 검증으로 판단하세요.'
              : 'Train with larger input images and stronger augmentation. Validate on separate test data and the production line before use.'}
          </p>

          <div className="flex flex-wrap gap-1.5 text-[10px] font-mono text-slate-300">
            <span className="bg-[#0B0E14] px-2 py-0.5 rounded-[2px] border border-[#2B3547] tabular-nums">
              <JargonTooltip termKey="learning_rate">학습률 0.0005 · 서서히 감소</JargonTooltip>
            </span>
            <span className="bg-[#0B0E14] px-2 py-0.5 rounded-[2px] border border-[#2B3547] tabular-nums">강한 이미지 변형</span>
            <span className="bg-[#0B0E14] px-2 py-0.5 rounded-[2px] border border-[#2B3547] tabular-nums">
              <JargonTooltip termKey="early_stopping">8회 개선 없으면 종료</JargonTooltip>
            </span>
            <span className="bg-[#0B0E14] px-2 py-0.5 rounded-[2px] border border-[#2B3547]">후보 모델용</span>
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
                <th className="py-1.5 px-3 text-blue-400">빠른 시험 학습 (A)</th>
                <th className="py-1.5 px-3 text-emerald-400">정밀 후보 학습 (B)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1E293B] text-slate-300">
              <tr>
                <td className="py-1.5 px-3 font-semibold text-slate-400">Target Line Process (적합 공정)</td>
                <td className="py-1.5 px-3">가능성 확인용 모델 후보</td>
                <td className="py-1.5 px-3">추가 검증용 모델 후보</td>
              </tr>
              <tr>
                <td className="py-1.5 px-3 font-semibold text-slate-400">학습 횟수 · 자동 종료</td>
                <td className="py-1.5 px-3 tabular-nums">최대 12회 (4회 개선 없으면 종료)</td>
                <td className="py-1.5 px-3 tabular-nums">최대 30회 (8회 개선 없으면 종료)</td>
              </tr>
              <tr>
                <td className="py-1.5 px-3 font-semibold text-slate-400">학습률</td>
                <td className="py-1.5 px-3 tabular-nums">0.001 (예열 뒤 서서히 감소)</td>
                <td className="py-1.5 px-3 tabular-nums">0.0005 (예열 뒤 서서히 감소)</td>
              </tr>
              <tr>
                <td className="py-1.5 px-3 font-semibold text-slate-400">한 번에 학습하는 이미지 수</td>
                <td className="py-1.5 px-3 tabular-nums">16장</td>
                <td className="py-1.5 px-3 tabular-nums">8장</td>
              </tr>
              <tr>
                <td className="py-1.5 px-3 font-semibold text-slate-400">이미지 변형(증강)</td>
                <td className="py-1.5 px-3">뒤집기, ±5° 회전, 20% 가리기</td>
                <td className="py-1.5 px-3">뒤집기, ±10° 회전, 35% 가리기</td>
              </tr>
              <tr>
                <td className="py-1.5 px-3 font-semibold text-slate-400">검출할 수 있는 결함 크기</td>
                <td className="py-1.5 px-3">데이터별 측정 필요</td>
                <td className="py-1.5 px-3">데이터별 측정 필요</td>
              </tr>
              <tr>
                <td className="py-1.5 px-3 font-semibold text-slate-400">Tact Time Estimate (소요 시간)</td>
                <td className="py-1.5 px-3">데이터 크기·장치에 따라 달라짐</td>
                <td className="py-1.5 px-3">데이터 크기·장치에 따라 달라짐</td>
              </tr>
              <tr>
                <td className="py-1.5 px-3 font-semibold text-slate-400">Inspection Standard (검사 규격)</td>
                <td className="py-1.5 px-3">별도 시험·현장 검증 필요</td>
                <td className="py-1.5 px-3">별도 시험·현장 검증 필요</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
