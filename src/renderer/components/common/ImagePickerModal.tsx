/**
 * src/renderer/components/common/ImagePickerModal.tsx
 * Steel Instrument Inspection Image Selection Dialog.
 */

import React, { useState } from 'react';
import {
  Check,
  Database,
  FileSearch,
  FolderOpen,
  Image as ImageIcon,
  Sparkles,
  X,
} from 'lucide-react';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useFlowchartStore } from '../../stores/useFlowchartStore';
import { resolveApiUrl } from '../../services/api';
import type { SelectedInspectionImage } from '../../types';

export interface ImagePickerModalProps {
  isOpen: boolean;
  onClose: () => void;
}

const SAMPLE_PRESETS: Array<{ name: string; path: string; desc: string }> = [
  {
    name: '웨이퍼 챔버 원본 결함 (039)',
    path: '/Users/kai/Downloads/운영서버/visual_inspection/chamber_039_orig.jpg',
    desc: '45MP 웨이퍼 챔버 고해상도 결함 이미지',
  },
  {
    name: '반도체 블랙 부품 Top View (L1-07)',
    path: '/Users/kai/Downloads/운영서버/test_crop_output/black_product_Top_View__C_Photo-L1-07.png',
    desc: '운영서버 실제 반도체 칩 탑뷰 검사 이미지',
  },
  {
    name: '하든드 검사 이미지 (L1-01)',
    path: '/Users/kai/Downloads/운영서버/test_crop_output/hardened_Top_View_M1-c_Photo-L1-01.png',
    desc: '표면 스크래치 및 보이드 검출 대상',
  },
];

export const ImagePickerModal: React.FC<ImagePickerModalProps> = ({ isOpen, onClose }) => {
  const { images, activeSplitFilter, setSplitFilter } = useDatasetStore();
  const { selectedImage, setSelectedImage } = useFlowchartStore();

  const [activeTab, setActiveTab] = useState<'dataset' | 'local'>('dataset');
  const [localPathInput, setLocalPathInput] = useState<string>(
    selectedImage?.source === 'file' ? selectedImage.imagePath : ''
  );
  const [tempSelected, setTempSelected] = useState<SelectedInspectionImage | null>(selectedImage);

  if (!isOpen) return null;

  const handleSelectFromDataset = (img: (typeof images)[0]) => {
    setTempSelected({
      source: 'dataset',
      imagePath: img.file_path,
      imageId: img.image_id,
      fileName: img.file_name,
      thumbnailUrl: img.thumbnail_url,
    });
  };

  const handleBrowseLocalFile = async () => {
    if (typeof window !== 'undefined' && (window as any).api?.selectFile) {
      const file = await (window as any).api.selectFile({
        title: '검사 대상 이미지 파일 선택',
        filters: [{ name: '이미지 파일', extensions: ['jpg', 'jpeg', 'png', 'bmp', 'webp', 'tif', 'tiff'] }],
      });
      if (file) {
        setLocalPathInput(file);
        setTempSelected({
          source: 'file',
          imagePath: file,
          fileName: file.split('/').pop() || 'local_image.jpg',
          thumbnailUrl: `/api/dataset/thumbnail/preview?file_path=${encodeURIComponent(file)}`,
        });
      }
    }
  };

  const handleApplyPreset = (preset: (typeof SAMPLE_PRESETS)[0]) => {
    setLocalPathInput(preset.path);
    setTempSelected({
      source: 'preset',
      imagePath: preset.path,
      fileName: preset.path.split('/').pop() || 'preset_image.png',
      thumbnailUrl: `/api/dataset/thumbnail/preview?file_path=${encodeURIComponent(preset.path)}`,
    });
  };

  const handleConfirm = () => {
    if (tempSelected) {
      setSelectedImage(tempSelected);
    } else if (localPathInput.trim()) {
      setSelectedImage({
        source: 'file',
        imagePath: localPathInput.trim(),
        fileName: localPathInput.trim().split('/').pop() || 'image.png',
        thumbnailUrl: `/api/dataset/thumbnail/preview?file_path=${encodeURIComponent(localPathInput.trim())}`,
      });
    }
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-6 select-none animate-in fade-in duration-100">
      <div className="bg-[#131822] border border-[#2B3547] rounded w-full max-w-3xl flex flex-col max-h-[85vh] shadow-2xl overflow-hidden">
        {/* Modal Header */}
        <div className="h-12 px-5 bg-[#0B0E14] border-b border-[#2B3547] flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <ImageIcon className="w-4 h-4 text-slate-300" />
            <h3 className="font-bold text-xs font-mono uppercase tracking-wider text-slate-100">
              검사 대상 이미지 선택 (Select Inspection Image)
            </h3>
          </div>
          <button
            onClick={onClose}
            className="p-1 hover:bg-[#1A212E] border border-transparent hover:border-[#2B3547] rounded text-slate-400 hover:text-white transition-colors cursor-pointer"
            title="닫기 (Esc)"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Modal Navigation Tabs */}
        <div className="px-5 pt-2 bg-[#0E131C] border-b border-[#2B3547] flex space-x-2 text-xs font-semibold">
          <button
            onClick={() => setActiveTab('dataset')}
            className={`px-3 py-2 flex items-center space-x-1.5 border-b-2 font-mono text-xs cursor-pointer transition-colors ${
              activeTab === 'dataset'
                ? 'border-[#10B981] text-slate-100 bg-[#131822] rounded-t font-bold'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Database className="w-3.5 h-3.5" />
            <span>임포트 데이터셋 ({images.length})</span>
          </button>

          <button
            onClick={() => setActiveTab('local')}
            className={`px-3 py-2 flex items-center space-x-1.5 border-b-2 font-mono text-xs cursor-pointer transition-colors ${
              activeTab === 'local'
                ? 'border-[#10B981] text-slate-100 bg-[#131822] rounded-t font-bold'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <FileSearch className="w-3.5 h-3.5" />
            <span>로컬 파일 / 운영서버 샘플</span>
          </button>
        </div>

        {/* Modal Body */}
        <div className="flex-1 overflow-y-auto p-5 bg-[#131822]">
          {activeTab === 'dataset' ? (
            <div>
              {/* Split Filters */}
              <div className="flex items-center space-x-1.5 mb-3.5">
                {(['all', 'train', 'val', 'test'] as const).map((split) => {
                  const isActive = activeSplitFilter === split;
                  return (
                    <button
                      key={split}
                      onClick={() => setSplitFilter(split)}
                      className={`px-2.5 py-1 rounded text-[11px] font-mono uppercase cursor-pointer transition-colors ${
                        isActive
                          ? 'bg-[#2B3547] text-white border border-[#3B4860] font-bold'
                          : 'bg-[#1A212E] text-slate-400 hover:text-slate-200 border border-[#2B3547]'
                      }`}
                    >
                      {split}
                    </button>
                  );
                })}
              </div>

              {images.length === 0 ? (
                <div className="text-center py-12 text-slate-500 text-xs font-mono">
                  <Database className="w-8 h-8 mx-auto mb-2 text-slate-500" />
                  <p>현재 임포트된 데이터셋 이미지가 없습니다.</p>
                  <p className="mt-1 text-slate-500">
                    '로컬 파일 / 운영서버 샘플' 탭에서 이미지를 선택하거나 Step 1에서 데이터셋을 불러오세요.
                  </p>
                </div>
              ) : (
                <div className="grid grid-cols-4 gap-2.5">
                  {images.map((img) => {
                    const isSelected = tempSelected?.imagePath === img.file_path;
                    return (
                      <div
                        key={img.image_id}
                        onClick={() => handleSelectFromDataset(img)}
                        className={`relative rounded border p-2 cursor-pointer transition-colors bg-[#1A212E] ${
                          isSelected
                            ? 'border-[#10B981] ring-1 ring-[#10B981]/60 bg-[#142320]'
                            : 'border-[#2B3547] hover:border-slate-500'
                        }`}
                      >
                        <div className="w-full h-24 rounded bg-[#0B0E14] border border-[#2B3547] overflow-hidden flex items-center justify-center mb-1.5">
                          <img
                            src={resolveApiUrl(img.thumbnail_url)}
                            alt={img.file_name}
                            className="w-full h-full object-cover"
                            loading="lazy"
                          />
                        </div>
                        <p className="text-[11px] font-mono font-bold text-slate-200 truncate">{img.file_name}</p>
                        <div className="flex items-center justify-between text-[10px] text-slate-400 mt-0.5">
                          <span className="truncate">{img.label || 'No Label'}</span>
                          <span className="uppercase text-[9px] text-slate-400 font-mono">{img.split}</span>
                        </div>
                        {isSelected && (
                          <div className="absolute top-1 right-1 w-4 h-4 bg-[#10B981] text-slate-950 rounded flex items-center justify-center font-bold">
                            <Check className="w-2.5 h-2.5 stroke-[3]" />
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-5">
              {/* Local File Browser */}
              <div>
                <label className="text-xs font-mono uppercase tracking-wider text-slate-300 block mb-1.5">
                  로컬 이미지 파일 경로 (Absolute File Path)
                </label>
                <div className="flex space-x-2">
                  <input
                    type="text"
                    value={localPathInput}
                    onChange={(e) => {
                      setLocalPathInput(e.target.value);
                      setTempSelected({
                        source: 'file',
                        imagePath: e.target.value,
                        fileName: e.target.value.split('/').pop() || 'image.png',
                        thumbnailUrl: `/api/dataset/thumbnail/preview?file_path=${encodeURIComponent(e.target.value)}`,
                      });
                    }}
                    placeholder="/Users/kai/Downloads/운영서버/..."
                    className="flex-1 bg-[#0B0E14] border border-[#2B3547] rounded px-3 py-1.5 text-xs font-mono text-slate-100 focus:outline-none focus:border-slate-400"
                  />
                  <button
                    onClick={handleBrowseLocalFile}
                    className="px-3.5 py-1.5 bg-[#1A212E] hover:bg-[#2B3547] border border-[#2B3547] rounded text-xs font-medium text-slate-200 flex items-center space-x-1.5 cursor-pointer transition-colors"
                  >
                    <FolderOpen className="w-3.5 h-3.5 text-slate-300" />
                    <span>파일 찾기...</span>
                  </button>
                </div>
              </div>

              {/* Sample Presets */}
              <div>
                <label className="text-xs font-mono uppercase tracking-wider text-slate-300 block mb-2 flex items-center space-x-1.5">
                  <Sparkles className="w-3.5 h-3.5 text-amber-400" />
                  <span>운영서버 반도체 제조 결함 샘플 프리셋 (1-Click Presets)</span>
                </label>
                <div className="space-y-1.5">
                  {SAMPLE_PRESETS.map((preset) => {
                    const isSelected = tempSelected?.imagePath === preset.path;
                    return (
                      <div
                        key={preset.path}
                        onClick={() => handleApplyPreset(preset)}
                        className={`p-2.5 rounded border cursor-pointer transition-colors flex items-center justify-between ${
                          isSelected
                            ? 'border-[#10B981] bg-[#142320]'
                            : 'border-[#2B3547] bg-[#1A212E] hover:border-slate-500'
                        }`}
                      >
                        <div>
                          <h4 className="text-xs font-bold text-slate-200">{preset.name}</h4>
                          <p className="text-[10px] text-slate-400 mt-0.5">{preset.desc}</p>
                          <p className="text-[9px] font-mono text-slate-500 truncate mt-0.5">{preset.path}</p>
                        </div>
                        <span className="text-[11px] font-mono font-bold text-slate-300 px-2.5 py-1 bg-[#131822] rounded border border-[#2B3547]">
                          선택
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Modal Footer */}
        <div className="h-14 px-5 border-t border-[#2B3547] flex items-center justify-between bg-[#0B0E14]">
          <div className="text-xs text-slate-400 truncate max-w-md font-mono">
            {tempSelected ? (
              <span className="text-[#10B981] font-medium truncate">
                ✓ 선택됨: {tempSelected.fileName}
              </span>
            ) : (
              <span>선택된 이미지가 없습니다.</span>
            )}
          </div>
          <div className="flex space-x-2">
            <button
              onClick={onClose}
              className="px-3.5 py-1.5 bg-[#1A212E] hover:bg-[#2B3547] border border-[#2B3547] rounded text-xs font-medium text-slate-300 cursor-pointer transition-colors"
            >
              취소
            </button>
            <button
              onClick={handleConfirm}
              className="px-4 py-1.5 bg-[#10B981] hover:bg-[#059669] active:bg-[#047857] text-slate-950 font-bold rounded text-xs border border-[#10B981] cursor-pointer transition-colors"
            >
              선택 확정
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ImagePickerModal;
