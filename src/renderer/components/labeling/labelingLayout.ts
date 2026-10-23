// The labeling screen's focus editing: the stacked full-width helper panels fold away so the canvas gets the height of
// the window (a large image at "fit" otherwise shows a few percent of its pixels on a 1440x900 screen). Remembered per
// viewer on this computer. Never folded: errors, the label set in use, the classes, the canvas tools, the team row
// (it loads the team settings and the label book and holds the shared editing lock) and a DICOM image's window controls.

const KEY = 'mv.labeling.focus';

/** The panels focus editing folds, in screen order (the last one sits below the class bar). */
export const LABELING_HELPER_PANELS = ['이미지 검토', '파생 이미지', '저장된 검토 대기열', '워크플로 영향', '모델 보조'] as const;
export type LabelingHelperPanel = typeof LABELING_HELPER_PANELS[number];

/** The switch keeps one name; whether focus editing is on is its pressed state. */
export const FOCUS_SWITCH_NAME = '집중 편집';

// Reading localStorage itself can throw (blocked site data), so it is read inside the callers' try.
const browserStorage = (): Storage | undefined => globalThis.localStorage;

export function readLabelingFocus(storage?: Pick<Storage, 'getItem'> | null, fallback = false): boolean {
  try {
    const stored = (storage === undefined ? browserStorage() : storage)?.getItem(KEY);
    return stored === '1' || stored === '0' ? stored === '1' : fallback;
  } catch {
    return fallback;
  }
}

/** The viewer's own 집중 편집 choice, or null when they never pressed it (the screen then follows the view mode). */
export function storedLabelingFocus(storage?: Pick<Storage, 'getItem'> | null): boolean | null {
  try {
    const stored = (storage === undefined ? browserStorage() : storage)?.getItem(KEY);
    return stored === '1' ? true : stored === '0' ? false : null;
  } catch {
    return null;
  }
}

export function writeLabelingFocus(focus: boolean, storage?: Pick<Storage, 'setItem'> | null): void {
  try {
    (storage === undefined ? browserStorage() : storage)?.setItem(KEY, focus ? '1' : '0');
  } catch {
    // A private window or blocked storage: the choice lasts for this session only.
  }
}

/** What focus editing folded, said while it is on (SAM2 lives in the model assist panel). */
export function focusStatus(focus: boolean): string {
  return focus ? `접은 패널: ${LABELING_HELPER_PANELS.map(name => name === '모델 보조' ? '모델 보조(SAM2)' : name).join(' · ')}` : '';
}

// Below this work area height there is little left to draw in: Fit keeps 32 px of padding above and below the image,
// and the mask controls (top) and the view readout (bottom) cover roughly 60 px and 35 px of the canvas, so a 240 px
// area leaves the image at most 176 px and about 145 px clear of both. 모두 보기 at 1440x900 in a classification project
// left a few pixels.
export const NARROW_CANVAS_HEIGHT = 240;

// The work area's share before the settings strip (the label set, and in 모두 보기 the derived image, saved queue and
// workflow impact panels and the specialist note) gets any height: the strip scrolls and gives way first. At 1440x900
// the rows that stay put (tools, the 집중 편집 row, the team row with the shared editing lock, image review, classes,
// drafts, model assist) and the filmstrip leave about 310 px, so the drawing area keeps about 248 px (just above
// NARROW_CANVAS_HEIGHT) and the strip about 60 px, the label set in use. Taller windows give the strip its full height
// first; the work area grows past this with the window. It is the literal flex-basis in LabelingStudio's work area
// class (Tailwind needs the literal).
export const WORK_AREA_BASIS = 248;
export const NARROW_CANVAS_NOTICE = '그림 영역이 좁습니다 · 집중 편집으로 보조 패널을 접을 수 있습니다';

/** The hint next to 집중 편집 while the open helper panels leave the work area short: '' when focus editing is on, the
 *  area has room, or it was never measured (no ResizeObserver). The screen never folds the panels by itself. */
export function narrowCanvasNotice(height: number | null, focus: boolean): string {
  return !focus && height !== null && height < NARROW_CANVAS_HEIGHT ? NARROW_CANVAS_NOTICE : '';
}

type View = { scale: number; offsetX: number; offsetY: number };
type Size = { width: number; height: number };

/** The view after the canvas area changed size: refitted when the image was shown at Fit for the previous size, else
 *  null (a view the user zoomed or panned stays as it is). */
export function refitAfterResize(view: View, previous: Size | null, next: Size, image: Size,
  fit: (width: number, height: number, imageWidth: number, imageHeight: number) => View): View | null {
  if (!previous || (previous.width === next.width && previous.height === next.height) || image.width <= 0 || image.height <= 0) return null;
  const before = fit(previous.width, previous.height, image.width, image.height);
  const atFit = Math.abs(view.scale - before.scale) <= 1e-9 * Math.max(1, before.scale)
    && Math.abs(view.offsetX - before.offsetX) <= 0.5 && Math.abs(view.offsetY - before.offsetY) <= 0.5;
  return atFit ? fit(next.width, next.height, image.width, image.height) : null;
}
