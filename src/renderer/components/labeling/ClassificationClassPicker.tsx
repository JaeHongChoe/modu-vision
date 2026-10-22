/**
 * Step 2 for a folder-based classification project: the image's class, where it comes from, and buttons to change it.
 * The choice is stored next to the project (the image file is never moved) and training, evaluation, the step-1
 * counts and the gallery all use it. Boxes and polygons stay available below for patch classification and evidence;
 * they never change the class.
 */
import React, { useEffect, useState } from 'react';
import { api } from '../../services/api';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useProjectStore } from '../../stores/useProjectStore';
import { bumpWorkflowEvidence } from '../../stores/workflowEvidence';
import { reportPanelError } from '../../stores/assistContext';

export const ClassificationClassPicker: React.FC = () => {
  const task = useProjectStore((state) => state.task);
  const image = useAnnotationStore((state) => state.currentImage);
  const applyImageClass = useAnnotationStore((state) => state.applyImageClass);
  const datasetClasses = Object.keys(useDatasetStore((state) => state.classes) || {});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState('');
  useEffect(() => { reportPanelError('이미지 클래스', error); return () => reportPanelError('이미지 클래스', null); }, [error]);
  if (task !== 'classification' || !image) return null;
  const current = image.label ?? null;
  const folder = image.folder_label ?? (image.class_source === 'override' ? null : current);
  const chosen = image.class_source === 'override';
  const classes = [...new Set([...datasetClasses, ...(current ? [current] : []), ...(folder ? [folder] : [])])];

  const choose = async (name: string | null) => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const result = await api.dataset.setImageClass({ image_path: image.file_path, class_name: name, actor: 'operator' });
      applyImageClass(image.file_path, result.class, result.class_source, result.metadata);
      setDraft('');
      // The counts and the saved split's per-class numbers follow the new class; evidence built on the old classes
      // becomes stale, so the footer re-reads it.
      void useDatasetStore.getState().annotationsChanged();
      bumpWorkflowEvidence();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section id="image-class-picker" tabIndex={-1} aria-label="이미지 클래스" className="flex flex-wrap items-center gap-2 border-b border-slate-800 bg-slate-950 px-4 py-2 text-xs">
      <span className="font-semibold text-slate-200">이 이미지의 클래스</span>
      <div role="group" aria-label="이미지 클래스 고르기" className="flex flex-wrap gap-1.5">
        {classes.map((name) => (
          <button key={name} type="button" disabled={busy} aria-pressed={name === current} onClick={() => void choose(name)}
            className={`rounded border px-2.5 py-1 font-semibold disabled:opacity-50 ${name === current
              ? 'border-cyan-400 bg-cyan-900/50 text-cyan-50' : 'border-slate-600 text-slate-200 hover:bg-slate-800'}`}>
            {name}
          </button>
        ))}
      </div>
      <form className="flex items-center gap-1" onSubmit={(event) => { event.preventDefault(); if (draft.trim()) void choose(draft.trim()); }}>
        <input aria-label="새 클래스 이름" value={draft} onChange={(event) => setDraft(event.target.value)} maxLength={100}
          placeholder="새 클래스" className="w-24 rounded border border-slate-600 bg-slate-900 px-2 py-1 text-slate-100" />
        <button type="submit" disabled={busy || !draft.trim()} className="rounded border border-slate-600 px-2 py-1 text-slate-200 disabled:opacity-40">추가</button>
      </form>
      <span className="text-slate-400">
        {chosen ? `2단계에서 지정함 · 폴더는 ${folder ?? '없음'}` : '폴더 이름 기준'} · 원본 파일은 옮기지 않습니다
      </span>
      {chosen && (
        <button type="button" disabled={busy} onClick={() => void choose(null)} className="rounded border border-slate-600 px-2 py-1 text-slate-200 disabled:opacity-40">
          폴더 클래스로 되돌리기
        </button>
      )}
      {error && <p role="alert" className="w-full text-rose-200">{error}</p>}
    </section>
  );
};
