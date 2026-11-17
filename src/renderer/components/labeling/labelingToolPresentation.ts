import type { ToolType, VisionTask } from '../../types';

/** A display context only. This never changes a project's task or active tool. */
export type LabelingTask = VisionTask | 'rotated_detection';
export const LABELING_DRAWING_TOOLS = ['select', 'bbox', 'rotated_bbox', 'polygon', 'brush', 'eraser', 'auto_select'] as const;
export type LabelingDrawingTool = typeof LABELING_DRAWING_TOOLS[number];

const PRIMARY_TOOLS: Record<LabelingTask, readonly LabelingDrawingTool[]> = {
  classification: ['select'],
  detection: ['select', 'bbox'],
  rotated_detection: ['select', 'rotated_bbox'],
  segmentation: ['select', 'polygon', 'brush', 'eraser', 'auto_select'],
  anomaly: ['select', 'polygon', 'brush', 'eraser', 'auto_select'],
};

export function labelingToolGroups(task: LabelingTask, simpleView: boolean, activeTool: ToolType): {
  primary: LabelingDrawingTool[]; other: LabelingDrawingTool[]; supportToolLabel?: string;
} {
  const preferred = PRIMARY_TOOLS[task];
  const primary = LABELING_DRAWING_TOOLS.filter(tool => !simpleView || preferred.includes(tool) || tool === activeTool);
  const other = LABELING_DRAWING_TOOLS.filter(tool => !primary.includes(tool));
  const supportToolLabel = activeTool === 'foundation_point' ? '모델 보조 점 선택'
    : activeTool === 'foundation_box' ? '모델 보조 박스 선택'
      : activeTool === 'shape_converter' ? '형상 변환' : undefined;
  return {primary, other, supportToolLabel};
}
