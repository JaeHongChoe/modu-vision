import { useLayoutEffect, useId, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

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
    ref.current?.querySelector<HTMLButtonElement>('button')?.focus();
    return () => {
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, []);
  return createPortal(<div ref={overlay} className="workspace-overlay" onMouseDown={event => { if (event.target === event.currentTarget) { event.preventDefault(); onClose(); } }}>
    <div ref={ref} role="dialog" aria-modal="true" className="workspace-dialog product-workbench" aria-labelledby={titleId}
    aria-describedby={description ? descriptionId : undefined}
    onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(); }
      if (event.key !== 'Tab') return;
      const controls = Array.from(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])') || [])
        .filter(control => !control.hidden && control.getClientRects().length > 0 && getComputedStyle(control).visibility !== 'hidden');
      const first = controls[0], last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }}>
    <header className="workspace-dialog__header"><div><h2 id={titleId}>{title}</h2>
      {description && <p id={descriptionId} className="workspace-description">{description}</p>}</div>
      <button type="button" aria-label={`${title} 닫기`} className="workspace-button" onClick={onClose}>닫기</button>
    </header>
    <div className="workspace-dialog__body">{children}</div>
    </div>
  </div>, document.body);
}
