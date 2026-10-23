/** Technical details: how the gallery's thumbnail reads ended, with the reads that need explaining (audit B5). */
import { useEffect, useState } from 'react';
import { subscribeThumbnailDiagnostics, thumbnailDiagnostics } from '../../stores/thumbnailDiagnostics';

const OUTCOME: Record<string, string> = { failed: '실패', stale: '버림', aborted: '멈춤' };

export function ThumbnailDiagnostics() {
  const [view, setView] = useState(thumbnailDiagnostics);
  useEffect(() => subscribeThumbnailDiagnostics(() => setView(thumbnailDiagnostics())), []);
  const { counts, records } = view;
  return <div className="flex gap-1.5"><dt className="text-slate-400">썸네일 읽기</dt><dd>
    <details><summary className="cursor-pointer">성공 {counts.ok} · 실패 {counts.failed} · 버림 {counts.stale} · 멈춤 {counts.aborted}</summary>
      {records.length ? <ol aria-label="썸네일 읽기 기록" className="mt-1 max-h-40 overflow-auto text-xs">{records.map((row, index) =>
        <li key={`${row.at}-${index}`}>{row.at.slice(11, 19)} · {OUTCOME[row.outcome] || row.outcome} · {row.file} · 프로젝트 {row.project || '없음'}{row.detail ? ` · ${row.detail}` : ''}</li>)}</ol>
        : <p className="mt-1 text-xs text-slate-400">설명이 필요한 읽기가 없습니다.</p>}
    </details></dd></div>;
}
