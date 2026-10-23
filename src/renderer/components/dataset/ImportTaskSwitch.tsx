import React, { useState } from 'react';
import { useProjectStore } from '../../stores/useProjectStore';
import type { VisionTask } from '../../types';

/** When an import is refused because the folder fits another inspection type (a LabelMe NG folder opened as
 * classification), one button switches the project's type and imports the same folder again. The refusal text
 * stays; the switch goes through the same task-change path as the header's selector. */
export const ImportTaskSwitch: React.FC<{ error: string | null; folder: string | null; onImport: (folder: string, task: VisionTask) => Promise<void> }> = ({ error, folder, onImport }) => {
  const updateTask = useProjectStore((state) => state.updateTask);
  const busy = useProjectStore((state) => state.isProjectBusy);
  const [working, setWorking] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  if (!error || !folder || !/영역 분할과 객체 검출을 학습할 수 있습니다/.test(error)) return null;
  const retry = async (task: VisionTask) => {
    setWorking(true); setFailure(null);
    try {
      const outcome = await updateTask(task);
      if (!outcome.ok) { setFailure(outcome.error); return; }
      await onImport(folder, task);
    } finally { setWorking(false); }
  };
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
      <span className="text-slate-200">이 폴더에 맞는 검사 종류로 바꿔서 다시 가져올 수 있습니다:</span>
      <button type="button" disabled={busy || working} onClick={() => void retry('segmentation')} className="workspace-button workspace-button--primary">영역 분할로 바꾸고 가져오기</button>
      <button type="button" disabled={busy || working} onClick={() => void retry('detection')} className="workspace-button">객체 검출로 바꾸고 가져오기</button>
      {failure && <span role="alert" className="text-red-200">{failure}</span>}
    </div>
  );
};
