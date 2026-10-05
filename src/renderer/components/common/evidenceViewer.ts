export interface EvidenceLayer {
  id: string; label: string; image: string;
  space: string; size?: [number, number];
}
export interface EvidenceView {
  key: string; title: string; imagePath?: string; imageSha256?: string;
  runId?: string; versionId?: string; nodeId?: string; roiId?: string;
  layers: EvidenceLayer[]; boxes?: Array<{id: string; box: number[]; space: string}>;
  facts?: Record<string, unknown>; warning?: string;
}
export const boundedZoom = (zoom: number) => Number.isFinite(zoom) ? Math.min(8, Math.max(.25, zoom)) : 1;
export const safeSnapshot = (value: string) => /^data:image\/(?:png|jpeg|webp|bmp);base64,[A-Za-z0-9+/=\r\n]+$/.test(value);
const validSize = (size: number[] | undefined): size is [number,number] => !!size && size.length===2 && size.every(value=>Number.isFinite(value)&&value>0);
export const validBox = (box: number[], size: number[] | undefined) => validSize(size) && box.length===4 && box.every(Number.isFinite)
  && box[0]>=0 && box[1]>=0 && box[2]>box[0] && box[3]>box[1] && box[2]<=size[0] && box[3]<=size[1];
export const canBlend = (a: EvidenceLayer, b: EvidenceLayer) => a.id!==b.id && a.space===b.space
  && validSize(a.size) && validSize(b.size) && a.size.every((value,index)=>value===b.size![index]) && safeSnapshot(a.image) && safeSnapshot(b.image);
