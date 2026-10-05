/**
 * src/renderer/components/common/ImagePickerModal.tsx
 * Steel Instrument Inspection Image Selection Dialog.
 */

import React, { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  Check,
  Database,
  FileSearch,
  FolderOpen,
  Image as ImageIcon,
  X,
} from 'lucide-react';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useFlowchartStore } from '../../stores/useFlowchartStore';
import { useProjectStore } from '../../stores/useProjectStore';
import { api, getProjectContextGeneration, subscribeProjectContext, resolveApiUrl, type LibraryResolution } from '../../services/api';
import { host } from '../../services/hostAdapter';
import type { ImageMeta, SelectedInspectionImage } from '../../types';
import { ImageLibraryBrowser } from './ImageLibraryBrowser';
import { identityOf, recallSelection, rememberSelection, resolutionNotice, selectionFromImage } from './librarySelection';

export interface ImagePickerModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const ImagePickerModal: React.FC<ImagePickerModalProps> = ({ isOpen, onClose }) => {
  const dialogRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (!isOpen) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialogRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
    return () => { if (previous?.isConnected) previous.focus({ preventScroll: true }); };
  }, [isOpen]);
  const folderPath = useDatasetStore((state) => state.folderPath);
  const task = useProjectStore((state) => state.task);
  const projectId = useProjectStore((state) => state.project?.id ?? null);
  const labelset = useProjectStore((state) => state.project?.active_labelset_id ?? 'default');
  const epoch = useSyncExternalStore(subscribeProjectContext, getProjectContextGeneration, getProjectContextGeneration);
  const scope = JSON.stringify([epoch, projectId, folderPath, task, labelset]);
  const openedScope = useRef<string | null>(null);
  const { selectedImage, setSelectedImage } = useFlowchartStore();
  // The validated revision is browsed by identity; without an accepted revision the older path listing is shown.
  const [library, setLibrary] = useState<'available' | 'unavailable'>('available');
  const [libraryReason, setLibraryReason] = useState<string | null>(null);
  const [savedCheck, setSavedCheck] = useState<LibraryResolution | null>(null);
  const [resolveError, setResolveError] = useState<string | null>(null);
  const userPicked = useRef(false);
  const pick = (selection: SelectedInspectionImage | null) => { userPicked.current = true; setTempSelected(selection); };

  const [activeTab, setActiveTab] = useState<'dataset' | 'local'>('dataset');
  const [localPathInput, setLocalPathInput] = useState<string>(
    selectedImage?.source === 'file' ? selectedImage.imagePath : ''
  );
  const [tempSelected, setTempSelected] = useState<SelectedInspectionImage | null>(selectedImage);
  const [datasetImages, setDatasetImages] = useState<ImageMeta[]>([]);
  const [datasetTotal, setDatasetTotal] = useState(0);
  const [datasetPage, setDatasetPage] = useState(1);
  const [datasetSplit, setDatasetSplit] = useState<'all' | 'train' | 'val' | 'test'>('all');
  const [datasetLoading, setDatasetLoading] = useState(false);
  const [datasetError, setDatasetError] = useState<string | null>(null);
  const [datasetRetry, setDatasetRetry] = useState(0);
  const pageSize = 48;
  const pageCount = Math.max(1, Math.ceil(datasetTotal / pageSize));

  useEffect(() => {
    if (!isOpen) return;
    const sameScope = openedScope.current === scope;
    openedScope.current = scope;
    // Global flow state can outlive this modal or its former project. An identity
    // restores only from this account's scoped preference; a path-only choice is
    // reused only after this modal has already opened in the same live scope.
    const remembered = projectId ? recallSelection(projectId) : null;
    const initial = identityOf(selectedImage) ? remembered : sameScope ? selectedImage : null;
    setTempSelected(initial);
    setLocalPathInput(initial?.source === 'file' ? initial.imagePath : '');
    setDatasetPage(1);
    setDatasetSplit('all');
    setLibrary('available');
    setLibraryReason(null);
    setSavedCheck(null);
    // A saved choice is checked against the current revision before it is offered again: a moved, replaced or missing
    // image is reported and never silently swapped for whatever now sits at its path.
    const saved = identityOf(initial) ? initial : remembered;
    const identity = identityOf(saved);
    userPicked.current = false;
    setResolveError(null);
    if (!saved || !identity) return;
    let cancelled = false;
    api.library.resolve([identity]).then(({ results }) => {
      if (cancelled || epoch !== getProjectContextGeneration()) return;
      const [result] = results;
      setSavedCheck(result);
      // a choice the user made while the check ran is theirs; the answer only explains the saved one
      if (!userPicked.current) setTempSelected(result.status === 'found' && result.current ? { ...saved, imagePath: result.current.file_path } : null);
    }).catch((caught) => {
      if (cancelled || epoch !== getProjectContextGeneration() || (caught as { status?: number }).status === 409) return;
      setResolveError(`저장된 선택을 확인하지 못했습니다(${caught instanceof Error ? caught.message : String(caught)}). 확인되지 않은 경로는 쓰지 않으니 다시 선택하세요.`);
      if (!userPicked.current) setTempSelected(null);
    });
    return () => { cancelled = true; };
  }, [isOpen, scope]);

  useEffect(() => {
    setDatasetPage(1);
  }, [folderPath]);

  useEffect(() => {
    if (!isOpen || activeTab !== 'dataset' || library !== 'unavailable') return;
    let cancelled = false;
    setDatasetLoading(true);
    setDatasetError(null);
    api.dataset.getImages({
      folder_path: folderPath,
      task,
      limit: pageSize,
      offset: (datasetPage - 1) * pageSize,
      split: datasetSplit === 'all' ? undefined : datasetSplit,
    }).then((response) => {
      if (cancelled || epoch !== getProjectContextGeneration()) return;
      setDatasetImages(response.items || []);
      setDatasetTotal(response.total || 0);
    }).catch((error) => {
      if (cancelled || epoch !== getProjectContextGeneration()) return;
      setDatasetImages([]);
      setDatasetTotal(0);
      setDatasetError(error instanceof Error ? error.message : '이미지 목록을 불러오지 못했습니다.');
    }).finally(() => {
      if (!cancelled && epoch === getProjectContextGeneration()) setDatasetLoading(false);
    });
    return () => { cancelled = true; };
  }, [isOpen, activeTab, folderPath, task, datasetPage, datasetSplit, library, datasetRetry, scope]);

  if (!isOpen) return null;

  const handleSelectFromDataset = (img: ImageMeta) => {
    pick({
      source: 'dataset',
      imagePath: img.file_path,
      imageId: img.image_id,
      fileName: img.file_name,
      thumbnailUrl: img.thumbnail_url,
    });
  };

  const handleBrowseLocalFile = async () => {
    if (host.can('pickPaths')) {
      const file = await host.selectFile({
        title: '검사 대상 이미지 파일 선택',
        filters: [{ name: '이미지 파일', extensions: ['jpg', 'jpeg', 'png', 'bmp', 'webp', 'tif', 'tiff'] }],
      });
      if (file && epoch === getProjectContextGeneration()) {
        setLocalPathInput(file);
        pick({
          source: 'file',
          imagePath: file,
          fileName: file.split('/').pop() || 'local_image.jpg',
          thumbnailUrl: `/api/dataset/thumbnail/preview?file_path=${encodeURIComponent(file)}`,
        });
      }
    }
  };

  const handleConfirm = () => {
    if (epoch !== getProjectContextGeneration() || openedScope.current !== scope) return;
    if (tempSelected) {
      setSelectedImage(tempSelected);
      if (projectId) rememberSelection(projectId, tempSelected);
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
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-label="검사 대상 이미지 선택" className="bg-[#131822] border border-[#2B3547] rounded w-full max-w-3xl flex flex-col max-h-[85vh] shadow-2xl overflow-hidden"
        onKeyDown={event => {
          if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(); return; }
          if (event.key !== 'Tab') return;
          const controls = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])') || [])
            .filter(control => !control.hidden && control.getClientRects().length > 0 && getComputedStyle(control).visibility !== 'hidden');
          const first = controls[0], last = controls.at(-1);
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }}>
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
            <span>{library === 'available' ? '검증된 데이터 버전' : `임포트 데이터셋 (${datasetTotal})`}</span>
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
            <span>로컬 파일</span>
          </button>
        </div>

        {/* Modal Body */}
        <div className="flex-1 overflow-y-auto p-5 bg-[#131822]">
          {activeTab === 'dataset' && library === 'available' ? (
            <div className="flex h-[52vh] flex-col gap-2">
              {resolveError && <div role="alert" className="rounded border border-red-600/50 bg-red-950/30 p-2 text-[11px] leading-5 text-red-200">{resolveError}</div>}
              {resolutionNotice(savedCheck) && (
                <div role="status" className="rounded border border-amber-600/50 bg-amber-950/30 p-2 text-[11px] leading-5 text-amber-200">
                  <p>{resolutionNotice(savedCheck)}</p>
                  {savedCheck?.status === 'moved' && (
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {savedCheck.candidates.map((candidate) => (
                        <button key={candidate.image_uuid} type="button" className="rounded border border-amber-500/60 px-2 py-0.5 font-mono text-[10px]"
                          onClick={() => pick({ source: 'dataset', imagePath: candidate.file_path, imageId: candidate.image_uuid,
                            imageUuid: candidate.image_uuid, sha256: candidate.sha256, relativePath: candidate.relative_path,
                            fileName: candidate.relative_path.split('/').pop() || candidate.relative_path,
                            thumbnailUrl: `/api/dataset/thumbnail/preview?file_path=${encodeURIComponent(candidate.file_path)}` })}>
                          {candidate.relative_path} 선택
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {tempSelected?.source === 'dataset' && tempSelected.imageUuid && (
                <div className="truncate text-[11px] text-slate-400">선택: <span className="font-mono text-slate-200">{tempSelected.relativePath}</span></div>
              )}
              <ImageLibraryBrowser
                selectedIds={new Set(tempSelected?.imageUuid ? [tempSelected.imageUuid] : [])}
                onPick={(item) => pick(selectionFromImage(item))}
                onUnavailable={(reason) => { setLibrary('unavailable'); setLibraryReason(reason); }}
              />
            </div>
          ) : activeTab === 'dataset' ? (
            <div>
              {libraryReason && (
                <div role="status" className="mb-3 rounded border border-slate-600/60 bg-[#1A212E] p-2 text-[11px] leading-5 text-slate-300">
                  검증된 데이터 버전이 없어 폴더 목록을 보여 줍니다. 이 목록에서 고른 이미지는 경로로만 기억되므로, '검증된 데이터 버전'에서 전체 검증 후 채택하면 이미지가 옮겨지거나 바뀌어도 알 수 있습니다.
                </div>
              )}
              {/* Split Filters */}
              <div className="flex items-center space-x-1.5 mb-3.5">
                {(['all', 'train', 'val', 'test'] as const).map((split) => {
                  const isActive = datasetSplit === split;
                  return (
                    <button
                      key={split}
                      onClick={() => { setDatasetSplit(split); setDatasetPage(1); }}
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

              <div className="flex items-center justify-between mb-3 text-[11px] font-mono text-slate-400">
                <span>
                  {datasetTotal > 0
                    ? `${(datasetPage - 1) * pageSize + 1}–${Math.min(datasetPage * pageSize, datasetTotal)} / ${datasetTotal}장`
                    : '0장'}
                </span>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setDatasetPage((page) => Math.max(1, page - 1))}
                    disabled={datasetLoading || datasetPage <= 1}
                    className="px-2 py-1 bg-[#1A212E] border border-[#2B3547] rounded disabled:opacity-40"
                  >
                    이전
                  </button>
                  <span className="text-slate-200 tabular-nums">{datasetPage} / {pageCount}</span>
                  <button
                    onClick={() => setDatasetPage((page) => Math.min(pageCount, page + 1))}
                    disabled={datasetLoading || datasetPage >= pageCount}
                    className="px-2 py-1 bg-[#1A212E] border border-[#2B3547] rounded disabled:opacity-40"
                  >
                    다음
                  </button>
                </div>
              </div>

              {datasetLoading ? (
                <div className="text-center py-12 text-slate-400 text-xs font-mono">이미지 목록을 불러오는 중...</div>
              ) : datasetError ? (
                <div role="alert" className="text-center py-12 text-rose-300 text-xs font-mono">
                  <p>{datasetError}</p>
                  <button type="button" onClick={() => setDatasetRetry(value => value + 1)}
                    className="mt-3 rounded border border-rose-800 px-3 py-1">이미지 목록 다시 불러오기</button>
                </div>
              ) : datasetImages.length === 0 ? (
                <div className="text-center py-12 text-slate-500 text-xs font-mono">
                  <Database className="w-8 h-8 mx-auto mb-2 text-slate-500" />
                  <p>현재 임포트된 데이터셋 이미지가 없습니다.</p>
                  <p className="mt-1 text-slate-500">
                    '로컬 파일' 탭에서 이미지를 선택하거나 Step 1에서 데이터셋을 불러오세요.
                  </p>
                </div>
              ) : (
                <div className="grid grid-cols-4 gap-2.5">
                  {datasetImages.map((img) => {
                    const isSelected = tempSelected?.imagePath === img.file_path;
                    return (
                      <button type="button" aria-pressed={isSelected} aria-label={`${img.file_name} 검사 이미지 선택`}
                        key={img.file_path}
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
                      </button>
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
                      pick(e.target.value.trim() ? {
                        source: 'file',
                        imagePath: e.target.value,
                        fileName: e.target.value.split('/').pop() || 'image.png',
                        thumbnailUrl: `/api/dataset/thumbnail/preview?file_path=${encodeURIComponent(e.target.value)}`,
                      } : null);
                    }}
                    placeholder="예: /path/to/inspection-image.png"
                    className="flex-1 bg-[#0B0E14] border border-[#2B3547] rounded px-3 py-1.5 text-xs font-mono text-slate-100 focus:outline-none focus:border-slate-400"
                  />
                  <button
                    onClick={handleBrowseLocalFile}
                    disabled={!host.can('pickPaths')}
                    title={host.can('pickPaths') ? undefined : '브라우저에서는 파일 선택 창을 열 수 없습니다. 경로를 직접 입력하세요.'}
                    className="px-3.5 py-1.5 bg-[#1A212E] hover:bg-[#2B3547] border border-[#2B3547] rounded text-xs font-medium text-slate-200 flex items-center space-x-1.5 cursor-pointer transition-colors"
                  >
                    <FolderOpen className="w-3.5 h-3.5 text-slate-300" />
                    <span>파일 찾기...</span>
                  </button>
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
              disabled={!tempSelected && !localPathInput.trim()}
              className="px-4 py-1.5 bg-[#10B981] hover:bg-[#059669] active:bg-[#047857] text-slate-950 font-bold rounded text-xs border border-[#10B981] cursor-pointer transition-colors disabled:opacity-40"
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
