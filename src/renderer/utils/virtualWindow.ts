/** Which rows of a fixed-height list are worth rendering: only the visible ones plus a small overscan. */
export interface RowWindow { start: number; end: number; offsetTop: number; totalHeight: number }

export function visibleRows(options: { scrollTop: number; viewportHeight: number; rowHeight: number; rowCount: number; overscan?: number }): RowWindow {
  const { scrollTop, viewportHeight, rowHeight, rowCount } = options;
  const overscan = options.overscan ?? 2;
  if (rowCount <= 0 || rowHeight <= 0) return { start: 0, end: 0, offsetTop: 0, totalHeight: 0 };
  const first = Math.max(0, Math.floor(Math.max(0, scrollTop) / rowHeight) - overscan);
  const visible = Math.ceil(Math.max(0, viewportHeight) / rowHeight) + 1;
  const end = Math.min(rowCount, first + visible + overscan * 2);
  return { start: first, end, offsetTop: first * rowHeight, totalHeight: rowCount * rowHeight };
}

/** Columns that fit a width, at least one. */
export function columnsFor(width: number, minimumItemWidth: number, gap = 0): number {
  if (width <= 0 || minimumItemWidth <= 0) return 1;
  return Math.max(1, Math.floor((width + gap) / (minimumItemWidth + gap)));
}

/** Whether the rendered window reaches the last `threshold` rows, so the next page should be requested. */
export function nearEnd(window: RowWindow, rowCount: number, threshold = 3): boolean {
  return rowCount === 0 || window.end >= rowCount - threshold;
}
