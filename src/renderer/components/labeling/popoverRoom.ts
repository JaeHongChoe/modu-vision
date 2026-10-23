import { useEffect, useLayoutEffect, useState, type RefObject } from 'react';

type HorizontalBounds = {left: number; right: number};
type HorizontalPlacement = {left: number; width: number};

/** An absolute panel's left offset within its row, bounded by the viewport and every clipping work column. The
 *  preferred width may be wider than the entry row (the compact layout gives drafts the middle third). */
export function horizontalPopoverBounds(row: HorizontalBounds, clips: readonly HorizontalBounds[], viewportWidth: number,
  preferredWidth: number): HorizontalPlacement {
  const start = Math.max(0, ...clips.map(clip => clip.left)) + 12;
  const end = Math.min(viewportWidth, ...clips.map(clip => clip.right)) - 12;
  const width = Math.min(preferredWidth, Math.max(0, end - start));
  const left = Math.max(start, Math.min(row.right - 12 - width, end - width));
  return {left: left - row.left, width};
}

/** The draft panel also follows its work column becoming narrower when the assistant drawer reserves space, even
 *  when the window itself did not resize. Measuring in a layout effect avoids a clipped first frame on open. */
export function usePopoverHorizontalBounds(open: boolean, rowRef: RefObject<HTMLElement>, preferredWidth: number): HorizontalPlacement | undefined {
  const [placement, setPlacement] = useState<HorizontalPlacement>();
  useLayoutEffect(() => {
    const row = rowRef.current;
    if (!open || !row) return;
    const clips: HTMLElement[] = [];
    for (let node = row.parentElement; node; node = node.parentElement) {
      if (getComputedStyle(node).overflowX !== 'visible') clips.push(node);
    }
    const fit = () => {
      const next = horizontalPopoverBounds(row.getBoundingClientRect(), clips.map(node => node.getBoundingClientRect()), window.innerWidth, preferredWidth);
      setPlacement(previous => previous?.left === next.left && previous?.width === next.width ? previous : next);
    };
    fit();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(fit);
    observer?.observe(row);
    clips.forEach(node => observer?.observe(node));
    window.addEventListener('resize', fit);
    window.addEventListener('scroll', fit, true);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', fit);
      window.removeEventListener('scroll', fit, true);
    };
  }, [open, rowRef, preferredWidth]);
  return placement;
}

/** The height a panel hanging below its row may take: what is still on screen above the lowest edge it can show at (the
 *  window, the step footer, the work column that clips it), less a 12 px margin. A minimum height would clip its last
 *  controls when the assistant drawer makes the rows above it wrap. */
export function roomBelow(rowBottom: number, clipBottoms: readonly number[], viewportHeight: number): number {
  return Math.max(0, Math.floor(Math.min(viewportHeight, ...clipBottoms) - rowBottom - 12));
}

/** The room below `rowRef` while `open`, kept up to date on resize and on any scroll, so a step-2 popover's own buttons
 *  stay reachable wherever its row sits (undefined until measured: the panel's own CSS cap applies). */
export function usePopoverRoom(open: boolean, rowRef: RefObject<HTMLElement>): number | undefined {
  const [room, setRoom] = useState<number>();
  useEffect(() => {
    const row = rowRef.current;
    if (!open || !row) return;
    const ancestors: HTMLElement[] = [];
    for (let node = row.parentElement; node; node = node.parentElement) ancestors.push(node);
    const fit = () => {
      const clips: number[] = [];
      for (const node of ancestors) {
        if (getComputedStyle(node).overflowY !== 'visible') clips.push(node.getBoundingClientRect().bottom);
      }
      setRoom(roomBelow(row.getBoundingClientRect().bottom, clips, window.innerHeight));
    };
    fit();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(fit);
    // A preceding settings strip can grow and move this row without changing an ancestor's total height.
    const watched = new Set<Element>([row]);
    for (let node: Element | null = row; node; node = node.parentElement) {
      watched.add(node);
      for (let before = node.previousElementSibling; before; before = before.previousElementSibling) watched.add(before);
    }
    watched.forEach(node => observer?.observe(node));
    window.addEventListener('resize', fit);
    window.addEventListener('scroll', fit, true);
    return () => { observer?.disconnect(); window.removeEventListener('resize', fit); window.removeEventListener('scroll', fit, true); };
  }, [open, rowRef]);
  return room;
}
