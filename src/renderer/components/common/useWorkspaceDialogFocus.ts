import { useLayoutEffect, type KeyboardEvent, type RefObject } from 'react';

const controlsSelector = 'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])';
const visible = (element: HTMLElement) => {
  if (element.hidden || element.getClientRects().length === 0 || getComputedStyle(element).visibility === 'hidden') return false;
  // Chromium can retain layout rectangles for the skipped content of a closed details.
  for (let ancestor = element.parentElement; ancestor; ancestor = ancestor.parentElement) {
    if (ancestor instanceof HTMLDetailsElement && !ancestor.open) {
      const summary = Array.from(ancestor.children).find(child => child.tagName === 'SUMMARY');
      if (!summary?.contains(element)) return false;
    }
  }
  return true;
};

/** Shared workspace modal focus ownership, including dynamically disclosed controls. */
export function useWorkspaceDialogFocus(ref: RefObject<HTMLDivElement>, options: {
  initialFocus?: string;
  restoreTarget?: () => HTMLElement | null;
} = {}) {
  useLayoutEffect(() => {
    const dialog = ref.current;
    const active = document.activeElement;
    const previous = active instanceof HTMLElement && active !== document.body && !dialog?.contains(active) ? active : null;
    const initial = dialog?.querySelector<HTMLElement>(options.initialFocus || 'button');
    (initial && visible(initial) ? initial : dialog)?.focus({ preventScroll: true });
    return () => {
      const preferred = options.restoreTarget?.();
      const target = preferred?.isConnected ? preferred : previous?.isConnected && visible(previous) ? previous : null;
      if (target?.isConnected) target.focus({ preventScroll: true });
    };
    // Ownership is captured once per modal lifetime, not whenever a form or callback changes.
  }, []);

  return (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Tab' || event.defaultPrevented) return;
    const dialog = ref.current;
    const target = event.target;
    // React portal events bubble through the component tree, even when their DOM
    // belongs to a child modal. Only the nearest modal owns its keyboard event.
    if (!(target instanceof HTMLElement) || !dialog?.contains(target) || target.closest('[role="dialog"][aria-modal="true"]') !== dialog) return;
    const controls = Array.from(dialog?.querySelectorAll<HTMLElement>(controlsSelector) || []).filter(control => visible(control) && control.closest('[role="dialog"][aria-modal="true"]') === dialog);
    const first = controls[0], last = controls.at(-1);
    if (!first) { event.preventDefault(); dialog?.focus(); return; }
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !dialog?.contains(active) || active === dialog)) {
      event.preventDefault(); last?.focus();
    } else if (!event.shiftKey && (active === last || !dialog?.contains(active) || active === dialog)) {
      event.preventDefault(); first.focus();
    }
  };
}
