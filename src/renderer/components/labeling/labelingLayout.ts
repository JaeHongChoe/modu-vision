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

export function readLabelingFocus(storage?: Pick<Storage, 'getItem'> | null): boolean {
  try {
    return (storage === undefined ? browserStorage() : storage)?.getItem(KEY) === '1';
  } catch {
    return false;
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
