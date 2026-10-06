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

interface NavigableNode extends PositionedNode {
  id: string;
  data: {label: string; node_type: string; task?: string; model_job_id?: string};
}
const normalizedQuery = (value:string) => value.normalize('NFKC').trim().toLocaleLowerCase();
export function findFlowNodes<T extends NavigableNode>(nodes:readonly T[], query:string):T[] {
  const needle=normalizedQuery(query);
  return nodes.filter(node=>!needle||[node.id,node.data.label,node.data.node_type,node.data.task,node.data.model_job_id]
    .some(value=>typeof value==='string'&&normalizedQuery(value).includes(needle)));
}
const clampScroll=(value:number,content:number,visible:number)=>Math.max(0,Math.min(Math.max(0,content-visible),value));
export function flowNodeScrollTarget(node:PositionedNode,viewport:FlowchartViewport,canvas:CanvasSize) {
  return {
    left:clampScroll(viewport.offsetX+(node.position.x+FLOW_NODE_WIDTH/2)*viewport.scale-canvas.width/2,viewport.contentWidth,canvas.width),
    top:clampScroll(viewport.offsetY+(node.position.y+FLOW_NODE_HEIGHT/2)*viewport.scale-canvas.height/2,viewport.contentHeight,canvas.height),
  };
}
export function computeFlowchartMinimap(viewport:Pick<FlowchartViewport,'contentWidth'|'contentHeight'>,canvas:CanvasSize&{left:number;top:number}) {
  const width=180,height=96,padding=4;
  const scale=Math.min((width-2*padding)/Math.max(1,viewport.contentWidth),(height-2*padding)/Math.max(1,viewport.contentHeight));
  const offsetX=(width-viewport.contentWidth*scale)/2,offsetY=(height-viewport.contentHeight*scale)/2;
  return {width,height,scale,offsetX,offsetY,visible:{x:offsetX+canvas.left*scale,y:offsetY+canvas.top*scale,
    width:Math.min(canvas.width,viewport.contentWidth)*scale,height:Math.min(canvas.height,viewport.contentHeight)*scale}};
}
export function minimapScrollTarget(point:{x:number;y:number},map:ReturnType<typeof computeFlowchartMinimap>,viewport:Pick<FlowchartViewport,'contentWidth'|'contentHeight'>,canvas:CanvasSize) {
  return {left:clampScroll((point.x-map.offsetX)/map.scale-canvas.width/2,viewport.contentWidth,canvas.width),
    top:clampScroll((point.y-map.offsetY)/map.scale-canvas.height/2,viewport.contentHeight,canvas.height)};
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
