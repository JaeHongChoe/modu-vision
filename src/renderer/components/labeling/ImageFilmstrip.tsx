/**
 * src/renderer/components/labeling/ImageFilmstrip.tsx
 * Horizontal thumbnail navigation queue with status badges (Annotated / Normal).
 */

import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { resolveApiUrl } from '../../services/api';

export const ImageFilmstrip: React.FC = () => {
  const { images, currentImageIndex, selectImageByIndex, nextImage, prevImage } = useAnnotationStore();

  return (
    <div className="h-20 bg-slate-950 border-t border-slate-800 px-3 flex items-center space-x-2 select-none">
      <button
        onClick={prevImage}
        disabled={currentImageIndex <= 0}
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
              key={img.image_id}
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
          {images.length > 0 ? `${currentImageIndex + 1} / ${images.length}` : '0 / 0'}
        </span>
        <button
          onClick={nextImage}
          disabled={currentImageIndex >= images.length - 1}
          className="p-1 rounded hover:bg-slate-800 disabled:opacity-20 text-slate-400 cursor-pointer"
        >
          <ChevronRight className="w-5 h-5" />
        </button>
      </div>
    </div>
  );
};
