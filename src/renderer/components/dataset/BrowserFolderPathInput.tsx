import React, { useState } from 'react';
import { host } from '../../services/hostAdapter';

/** A browser has no native folder dialog. Typing the folder path the backend can read (a path on the machine that runs
 * the backend, or a mounted share) is the way in; the desktop app keeps its picker and never shows this. */
export const BrowserFolderPathInput: React.FC<{ disabled?: boolean; onImport: (folder: string) => Promise<void> }> = ({ disabled = false, onImport }) => {
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  if (host.kind() !== 'browser') return null;
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const folder = value.trim();
    if (!folder || busy) return;
    setBusy(true);
    try { await onImport(folder); } finally { setBusy(false); }
  };
  return (
    <form onSubmit={submit} className="flex items-center gap-2 text-xs">
      <label className="flex items-center gap-2 text-slate-300">
        <span className="whitespace-nowrap">이미지 폴더 경로</span>
        <input aria-label="이미지 폴더 경로" value={value} onChange={event => setValue(event.target.value)} disabled={disabled || busy}
          placeholder="예: /data/inspection/line-a (서버가 읽을 수 있는 경로)" className="w-64 rounded border border-slate-600 bg-[#0B0E14] px-2 py-1 text-slate-100" />
      </label>
      <button type="submit" disabled={disabled || busy || !value.trim()} className="whitespace-nowrap rounded border border-blue-400 bg-blue-600 px-3 py-1.5 font-semibold text-white disabled:opacity-50">
        {busy ? '불러오는 중…' : '경로로 불러오기'}
      </button>
    </form>
  );
};
