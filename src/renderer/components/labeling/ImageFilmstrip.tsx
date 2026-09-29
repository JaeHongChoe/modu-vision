/**
 * src/renderer/components/labeling/ImageFilmstrip.tsx
 * Horizontal thumbnail navigation queue with status badges (Annotated / Normal).
 */

import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { resolveApiUrl } from '../../services/api';

export const ImageFilmstrip: React.FC = () => {
  const { images, currentImageIndex, selectImageByIndex, nextImage, prevImage } = useAnnotationStore();
  const { page, pageSize, totalImagesCount, loadImages } = useDatasetStore();
  const pageCount = Math.max(1, Math.ceil(totalImagesCount / pageSize));
  const canGoBack = currentImageIndex > 0 || page > 1;
  const canGoForward = currentImageIndex < images.length - 1 || page < pageCount;

  const changePage = async (nextPage: number) => {
    const annotation = useAnnotationStore.getState();
    if (annotation.isDirty) {
      const saved = await annotation.saveAnnotations();
      if (!saved || useAnnotationStore.getState().isDirty) return;
    }
    await loadImages(nextPage);
    const dataset = useDatasetStore.getState();
    if (dataset.page === nextPage) {
      const targetIndex = nextPage < page ? dataset.images.length - 1 : 0;
      await useAnnotationStore.getState().setImages(dataset.images, targetIndex);
    }
  };

  const goBack = async () => {
    if (currentImageIndex > 0) await prevImage();
    else if (page > 1) await changePage(page - 1);
  };

  const goForward = async () => {
    if (currentImageIndex < images.length - 1) await nextImage();
    else if (page < pageCount) await changePage(page + 1);
  };

  return (
    <div className="h-20 bg-slate-950 border-t border-slate-800 px-3 flex items-center space-x-2 select-none">
      <button
        onClick={goBack}
        disabled={!canGoBack}
        aria-label="Previous image or page"
        className="p-1 rounded hover:bg-slate-800 disabled:opacity-20 text-slate-400 cursor-pointer"
      >
        <ChevronLeft className="w-5 h-5" />
      </button>

      <div className="flex-1 flex items-center space-x-2 overflow-x-auto py-1">
        {images.map((img, idx) => {
          const isSelected = idx === currentImageIndex;
          const thumbSrc = resolveApiUrl(
            img.thumbnail_url ||
              `/api/dataset/thumbnail/${encodeURIComponent(img.file_name)}?file_path=${encodeURIComponent(
                img.file_path
              )}&size=128`
          );

          return (
            <div
              key={img.file_path}
              onClick={() => selectImageByIndex(idx)}
              className={`relative flex-shrink-0 w-16 h-14 rounded-lg overflow-hidden cursor-pointer border-2 transition-all ${
                isSelected
                  ? 'border-blue-500 ring-2 ring-blue-500/30 shadow'
                  : 'border-slate-800 hover:border-slate-600 opacity-70 hover:opacity-100'
              }`}
            >
              <img src={thumbSrc} alt={img.file_name} className="w-full h-full object-cover bg-black" />
              <div className="absolute bottom-0 inset-x-0 bg-black/80 px-1 py-0.5 text-[9px] truncate text-slate-300 text-center font-mono">
                {img.image_id}
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex items-center space-x-2">
        <span className="text-xs font-mono text-slate-400">
          {images.length > 0
            ? `${(page - 1) * pageSize + currentImageIndex + 1} / ${totalImagesCount}`
            : `0 / ${totalImagesCount}`}
        </span>
        {pageCount > 1 && (
          <span className="text-[10px] font-mono text-slate-500">Page {page}/{pageCount}</span>
        )}
        <button
          onClick={goForward}
          disabled={!canGoForward}
          aria-label="Next image or page"
          className="p-1 rounded hover:bg-slate-800 disabled:opacity-20 text-slate-400 cursor-pointer"
        >
          <ChevronRight className="w-5 h-5" />
        </button>
      </div>
    </div>
  );
};
