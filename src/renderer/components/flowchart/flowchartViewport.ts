/** Fit a saved node graph into the visible canvas without changing its coordinates. */

export const FLOW_NODE_WIDTH = 272;
export const FLOW_NODE_HEIGHT = 220;

const CANVAS_PADDING = 48;
const MAX_AUTO_SCALE = 1.25;
const MIN_READABLE_SCALE = 0.72;

/** Initial editor scale; full-graph fit remains a separate user action. */
export function readableFlowScale(fitScale: number): number {
  return Math.max(MIN_READABLE_SCALE, fitScale);
}

interface PositionedNode {
  position: { x: number; y: number };
}

interface CanvasSize {
  width: number;
  height: number;
}

export interface FlowchartViewport {
  scale: number;
  offsetX: number;
  offsetY: number;
  layerWidth: number;
  layerHeight: number;
  contentWidth: number;
  contentHeight: number;
}

export function computeFlowchartViewport(
  nodes: readonly PositionedNode[],
  canvas: CanvasSize,
  zoomMultiplier = 1,
): FlowchartViewport {
  const width = Math.max(1, canvas.width);
  const height = Math.max(1, canvas.height);
  if (nodes.length === 0) {
    return {
      scale: 1, offsetX: 0, offsetY: 0,
      layerWidth: width, layerHeight: height,
      contentWidth: width, contentHeight: height,
    };
  }

  const minX = Math.min(...nodes.map((node) => node.position.x));
  const minY = Math.min(...nodes.map((node) => node.position.y));
  const maxX = Math.max(...nodes.map((node) => node.position.x + FLOW_NODE_WIDTH));
  const maxY = Math.max(...nodes.map((node) => node.position.y + FLOW_NODE_HEIGHT));
  const graphWidth = maxX - minX;
  const graphHeight = maxY - minY;
  const fitScale = Math.min(
    MAX_AUTO_SCALE,
    Math.max(1, width - 2 * CANVAS_PADDING) / graphWidth,
    Math.max(1, height - 2 * CANVAS_PADDING) / graphHeight,
  );
  const scale = fitScale * Math.max(0.25, Math.min(8, zoomMultiplier));
  const left = Math.max(CANVAS_PADDING, (width - graphWidth * scale) / 2);
  // A tall editor should open with the graph directly under the toolbar.
  // Vertical centering left a large empty strip before the first node.
  const top = Math.max(CANVAS_PADDING, Math.min(96, (height - graphHeight * scale) / 2));

  return {
    scale,
    offsetX: left - minX * scale,
    offsetY: top - minY * scale,
    layerWidth: Math.max(1, maxX + CANVAS_PADDING),
    layerHeight: Math.max(1, maxY + CANVAS_PADDING),
    contentWidth: Math.max(width, Math.ceil(left + graphWidth * scale + CANVAS_PADDING - 0.001)),
    contentHeight: Math.max(height, Math.ceil(top + graphHeight * scale + CANVAS_PADDING - 0.001)),
  };
}
