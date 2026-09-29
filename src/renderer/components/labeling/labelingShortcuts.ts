import type { ToolType } from '../../types';

type ShortcutEvent = {
  key: string;
  code?: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
  isComposing?: boolean;
};

type ShortcutAction =
  | { kind: 'tool'; tool: ToolType }
  | { kind: 'fit' | 'undo' | 'redo' };

const TOOL_KEYS: Record<string, ToolType> = {
  '1': 'select',
  '2': 'bbox',
  '3': 'rotated_bbox',
  '4': 'polygon',
  '5': 'brush',
  '6': 'eraser',
};

export function resolveLabelingShortcut(event: ShortcutEvent): ShortcutAction | null {
  if (event.isComposing || event.altKey) return null;
  const key = event.key.toLowerCase();
  if (event.metaKey || event.ctrlKey) {
    if (key === 'z') return { kind: event.shiftKey ? 'redo' : 'undo' };
    if (key === 'y' && !event.shiftKey) return { kind: 'redo' };
    return null;
  }
  // A Korean IME reports the physical F key as ㄹ. Keep the advertised F
  // shortcut tied to the key position while the canvas has focus.
  if (key === 'f' || event.code === 'KeyF') return { kind: 'fit' };
  const tool = TOOL_KEYS[key];
  return tool ? { kind: 'tool', tool } : null;
}
