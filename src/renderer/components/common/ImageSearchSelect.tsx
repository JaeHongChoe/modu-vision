import React, { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { api, getProjectContextGeneration, subscribeProjectContext, type LibraryImage } from '../../services/api';
import type { ImageMeta, VisionTask } from '../../types';

interface Props {
  /** Accessible name of the control (also the list's name). */
  label: string;
  /** The chosen image's file path ('' when none). */
  value: string;
  onChange: (path: string) => void;
  /** Opt-in identity selection. Existing consumers continue receiving paths through onChange. */
  onIdentityChange?: (image: LibraryImage | null) => void;
  /** Explicit path-only selection when no validated revision exists. */
  onLegacyChange?: (path: string) => void;
  disabled?: boolean;
  /** Pick the first image of the first page when nothing is chosen yet (a convenience for checks that need any image). */
  autoSelectFirst?: boolean;
  /** Only used without an accepted revision: the older listing of the first images of this folder. */
  folder?: string | null;
  task?: VisionTask;
}

const PAGE = 20;

/**
 * One image of the project's validated revision, found by searching its name on the server instead of scrolling the
 * first 64 entries. Without an accepted revision it shows the older list of the first images and says so.
 */
export const ImageSearchSelect: React.FC<Props> = ({ label, value, onChange, onIdentityChange, onLegacyChange, disabled, autoSelectFirst, folder, task }) => {
  const epoch = useSyncExternalStore(subscribeProjectContext, getProjectContextGeneration, getProjectContextGeneration);
  const listedEpoch = useRef<number | null>(null);
  const [text, setText] = useState('');
  const [items, setItems] = useState<LibraryImage[]>([]);
  const [legacy, setLegacy] = useState<ImageMeta[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const change = useRef(onChange);
  change.current = onChange;
  const identityChange = useRef(onIdentityChange);
  identityChange.current = onIdentityChange;
  const legacyChange = useRef(onLegacyChange);
  legacyChange.current = onLegacyChange;
  const initial = useRef(value);
  initial.current = value;

  useEffect(() => {
    let live = true;
    const current = () => live && epoch === getProjectContextGeneration();
    setItems([]);
    setLegacy(null);
    setError(null);
    const timer = window.setTimeout(() => {
      api.library.images({ q: text.trim() || undefined, limit: PAGE }).then((page) => {
        if (!current()) return;
        listedEpoch.current = epoch;
        setItems(page.items);
        setLegacy(null);
        setError(null);
        const firstValid = page.items.find((item) => item.valid);  // never an image the revision found corrupt
        if (autoSelectFirst && !initial.current && firstValid) {
          if (identityChange.current) identityChange.current(firstValid); else change.current(firstValid.file_path);
        }
      }).catch((caught) => {
        if (!current()) return;
        if ((caught as { status?: number }).status === 409 && folder) {
          api.dataset.getImages({ folder_path: folder, task, limit: 64 }).then((response) => {
            if (!current()) return;
            listedEpoch.current = epoch;
            setLegacy(response.items || []);
            setItems([]);
            setError(null);
            if (autoSelectFirst && !initial.current && response.items?.[0]) {
              if (legacyChange.current) legacyChange.current(response.items[0].file_path); else change.current(response.items[0].file_path);
            }
          }).catch((inner) => current() && setError(inner instanceof Error ? inner.message : String(inner)));
        } else {
          setError(caught instanceof Error ? caught.message : String(caught));
        }
      });
    }, 250);
    return () => { live = false; window.clearTimeout(timer); };
  }, [text, folder, task, autoSelectFirst, epoch]);

  const ready = listedEpoch.current === epoch && epoch === getProjectContextGeneration();

  if (legacy) {
    return (
      <div className="space-y-1">
        <select aria-label={label} disabled={disabled || !ready} value={value} onChange={(event) => { if (ready && epoch === getProjectContextGeneration()) (onLegacyChange || onChange)(event.target.value); }} className="w-full rounded bg-slate-800 p-2">
          <option value="">원본 이미지 선택</option>
          {value && !legacy.some((item) => item.file_path === value) && <option value={value}>{value.split(/[\\/]/).pop()} (현재 선택 · 목록에 없음)</option>}
          {legacy.map((item) => <option key={item.file_path} value={item.file_path}>{item.file_name}</option>)}
        </select>
        <p className="text-[11px] text-slate-500">검증된 데이터 버전이 없어 처음 64장만 보입니다. 전체 검증 후 채택하면 이름으로 찾을 수 있습니다.</p>
      </div>
    );
  }
  const chosen = items.find((item) => item.file_path === value);
  return (
    <div className="space-y-1">
      <input type="search" aria-label={`${label} 검색`} disabled={disabled} value={text} onChange={(event) => setText(event.target.value)}
        placeholder="파일 이름이나 폴더로 검색" className="w-full rounded bg-slate-800 p-2" />
      <select aria-label={label} disabled={disabled || !ready} value={value} onChange={(event) => {
        if (!ready || epoch !== getProjectContextGeneration()) return;
        if (!onIdentityChange) { onChange(event.target.value); return; }
        if (!event.target.value) { onIdentityChange(null); return; }
        const image = items.find((item) => item.file_path === event.target.value);
        if (image) onIdentityChange(image);
      }} size={Math.min(6, Math.max(2, items.length + 1))}
        className="w-full rounded bg-slate-800 p-1">
        <option value="">{items.length ? '이미지 선택' : '조건에 맞는 이미지가 없습니다'}</option>
        {value && !chosen && <option value={value}>{value.split(/[\\/]/).pop()} (현재 선택)</option>}
        {items.map((item) => <option key={item.image_uuid} value={item.file_path} disabled={Boolean(onIdentityChange && (!item.valid || !item.sha256))}>{item.relative_path}{item.valid ? '' : ` · ${item.error_code}`}</option>)}
      </select>
      {onIdentityChange && chosen?.valid && chosen.sha256 && /^[a-f0-9]{64}$/.test(chosen.sha256) && <button type="button" disabled={disabled || !ready}
        onClick={() => { if (ready && epoch === getProjectContextGeneration()) onIdentityChange(chosen); }} className="rounded border border-slate-600 px-3 py-2 disabled:opacity-40">현재 이미지 다시 선택</button>}
      {error && <p role="alert" className="text-[11px] text-red-300">{error}</p>}
    </div>
  );
};
