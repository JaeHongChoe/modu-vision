// Step 1's focus view: the stacked full-width panels above the toolbar fold away so the sidebar and the gallery get the
// height of the window (on a 1024x768 screen the body row shrank to 0 px and the sidebar showed only its padding).
// Remembered per viewer on this computer. Never folded: the step title, errors, the session notice and the toolbar
// (the folder button, the path, the Train/Val/Test counts and the switch itself).

const KEY = 'mv.dataset.focus';

/** The panels the focus view folds, in screen order (데이터 준비 상태 sits inside 데이터 관리 도구 in the simple view). */
export const DATASET_HELPER_PANELS = ['데이터 관리 도구', '데이터 현황', '데이터 준비 상태'] as const;
export type DatasetHelperPanel = typeof DATASET_HELPER_PANELS[number];

/** The switch keeps one name (not step 2's 집중 편집, which the browser specs look up by exact name); whether the focus
 *  view is on is its pressed state. */
export const FOCUS_SWITCH_NAME = '집중 보기';

// Reading localStorage itself can throw (blocked site data), so it is read inside the callers' try.
const browserStorage = (): Storage | undefined => globalThis.localStorage;

export function readDatasetFocus(storage?: Pick<Storage, 'getItem'> | null, fallback = false): boolean {
  try {
    const stored = (storage === undefined ? browserStorage() : storage)?.getItem(KEY);
    return stored === '1' || stored === '0' ? stored === '1' : fallback;
  } catch {
    return fallback;
  }
}

/** The viewer's own 집중 보기 choice, or null when they never pressed it (the screen then stays open, or folds once by
 *  itself when the body is unusable: autoFoldDecision). */
export function storedDatasetFocus(storage?: Pick<Storage, 'getItem'> | null): boolean | null {
  try {
    const stored = (storage === undefined ? browserStorage() : storage)?.getItem(KEY);
    return stored === '1' ? true : stored === '0' ? false : null;
  } catch {
    return null;
  }
}

export function writeDatasetFocus(focus: boolean, storage?: Pick<Storage, 'setItem'> | null): void {
  try {
    (storage === undefined ? browserStorage() : storage)?.setItem(KEY, focus ? '1' : '0');
  } catch {
    // A private window or blocked storage: the choice lasts for this session only.
  }
}

/** What the focus view folded, said while it is on. */
export function focusStatus(focus: boolean): string {
  return focus ? `접은 패널: ${DATASET_HELPER_PANELS.join(' · ')}` : '';
}

// Below this body height there is little left to work in: the sidebar needs the 학습 전 점검 heading and its first KPI
// row, and the gallery one M-density card row (144 px) under its filter toolbar, which is about 240 px. In px, not rem:
// the body is measured with getBoundingClientRect() and the threshold must not grow with the 18/20 px root font that
// index.css sets on 27-inch monitors (a card row is the same height there).
export const NARROW_BODY_HEIGHT = 240;
export const NARROW_BODY_NOTICE = '작업 영역이 좁습니다 · 집중 보기로 위쪽 패널을 접을 수 있습니다';

/** The hint next to 집중 보기 while the open panels leave the body short: '' when the focus view is on, the body has
 *  room, or it was never measured (no ResizeObserver). Above UNUSABLE_BODY_HEIGHT the screen never folds by itself. */
export function narrowBodyNotice(height: number | null, focus: boolean): string {
  return !focus && height !== null && height < NARROW_BODY_HEIGHT ? NARROW_BODY_NOTICE : '';
}

// Under this height the body is not short but gone (0 px at 1024x768: the sidebar showed its padding and the gallery
// nothing), which a first-time user cannot recover from without knowing the switch. So, unlike step 2, the screen folds
// the three panels once by itself for a viewer who never chose, and says so; a press of 집중 보기 always wins afterwards.
export const UNUSABLE_BODY_HEIGHT = 120;
export const AUTO_FOLD_STATUS = '위쪽 패널을 접었습니다 · 집중 보기를 눌러 다시 보일 수 있습니다';
/** The toolbar row never wraps, so below 1280px the status and the notice show these short forms (the full text is
 *  the span's title). */
export const FOLD_STATUS_SHORT = '위쪽 패널 접음';
export const NARROW_BODY_NOTICE_SHORT = '작업 영역 좁음';

/** Whether the screen folds the panels by itself: only for a viewer who never pressed 집중 보기 (storedChoice null) and a
 *  measured body under UNUSABLE_BODY_HEIGHT. A stored choice, either way, is the viewer's and is never overridden. */
export function autoFoldDecision(height: number | null, storedChoice: boolean | null): boolean {
  return storedChoice === null && height !== null && height < UNUSABLE_BODY_HEIGHT;
}
