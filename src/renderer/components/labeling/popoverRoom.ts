import { useEffect, useState, type RefObject } from 'react';

/** The height a panel hanging below its row may take: what is still on screen above the lowest edge it can show at (the
 *  window, the step footer, the work column that clips it), less a 12 px margin, never under 240 px. */
export function roomBelow(rowBottom: number, clipBottoms: readonly number[], viewportHeight: number): number {
  return Math.max(240, Math.floor(Math.min(viewportHeight, ...clipBottoms) - rowBottom - 12));
}

/** The room below `rowRef` while `open`, kept up to date on resize and on any scroll, so a step-2 popover's own buttons
 *  stay reachable wherever its row sits (undefined until measured: the panel's own CSS cap applies). */
export function usePopoverRoom(open: boolean, rowRef: RefObject<HTMLElement>): number | undefined {
  const [room, setRoom] = useState<number>();
  useEffect(() => {
    if (!open) return;
    const fit = () => {
      const row = rowRef.current;
      if (!row) return;
      const clips: number[] = [];
      for (let node = row.parentElement; node; node = node.parentElement) {
        if (getComputedStyle(node).overflowY !== 'visible') clips.push(node.getBoundingClientRect().bottom);
      }
      setRoom(roomBelow(row.getBoundingClientRect().bottom, clips, window.innerHeight));
    };
    fit();
    window.addEventListener('resize', fit);
    window.addEventListener('scroll', fit, true);
    return () => { window.removeEventListener('resize', fit); window.removeEventListener('scroll', fit, true); };
  }, [open, rowRef]);
  return room;
}
