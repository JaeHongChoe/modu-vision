import { useLayoutEffect, useId, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useWorkspaceDialogFocus } from './useWorkspaceDialogFocus';

export function WorkspaceDialog({ title, description, onClose, children }: {
  title: string; description?: string; onClose: () => void; children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const overlay = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  useLayoutEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    // An existing portalled workspace may open this dialog. Keep it above its
    // opener's stacking context without changing global error/modal layers.
    let layer = 149;
    for (let parent = previous; parent; parent = parent.parentElement) {
      const value = Number.parseInt(getComputedStyle(parent).zIndex, 10);
      if (Number.isFinite(value)) layer = Math.max(layer, value);
    }
    if (overlay.current) overlay.current.style.zIndex = String(layer + 1);
  }, []);
  const containFocus = useWorkspaceDialogFocus(ref);
  return createPortal(<div ref={overlay} className="workspace-overlay" onMouseDown={event => { if (event.target === event.currentTarget) { event.preventDefault(); onClose(); } }}>
    <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" className="workspace-dialog product-workbench" aria-labelledby={titleId}
    aria-describedby={description ? descriptionId : undefined}
    onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(); }
      containFocus(event);
    }}>
    <header className="workspace-dialog__header"><div><h2 id={titleId}>{title}</h2>
      {description && <p id={descriptionId} className="workspace-description">{description}</p>}</div>
      <button type="button" aria-label={`${title} 닫기`} className="workspace-button" onClick={onClose}>닫기</button>
    </header>
    <div className="workspace-dialog__body">{children}</div>
    </div>
  </div>, document.body);
}
