import type {FlowchartExecutionResult} from '../../types';

export interface EvidenceLayer {
  id: string; label: string; image: string;
  space: string; size?: [number, number];
  facts?: Record<string, unknown>;
}
export interface EvidenceView {
  key: string; title: string; imagePath?: string; imageSha256?: string;
  runId?: string; versionId?: string; graphSha256?: string; nodeId?: string; roiId?: string;
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

/** Keep raster and compressed numeric payloads out of the text inspector. */
export function compactEvidence(value: Record<string, unknown>): Record<string, unknown> {
  const fields = ['roi_id','source_node_id','label','bbox','source_bbox','source_transform','defect_score','score_spec','score_basis','verdict',
    'flaw_type','confidence','map_semantics','coordinate_space','predicted_class','defect_area_px','blob_count','largest_blob_area_px',
    'measurements','blob_measurements','recognized_text','ocr_regions','ocr_rule_result','final_verdict'];
  const facts: Record<string, unknown> = Object.fromEntries(fields.filter(key=>value[key]!==undefined).map(key=>[key,value[key]]));
  const numeric = value.anomaly_values;
  if(numeric && typeof numeric==='object') {
    const data=numeric as Record<string,unknown>;
    facts.anomaly_values={dtype:data.dtype,encoding:data.encoding,shape:data.shape};
  }
  if(Array.isArray(value.segmentation_classes)) facts.segmentation_classes=value.segmentation_classes.filter(row=>row && typeof row==='object' && !Array.isArray(row)).map(row=>{
    const {mask: _mask,...measurements}=row as Record<string,unknown>;return measurements;
  });
  return facts;
}

/** A stored crop, mask or model grid lacks a shared-source raster contract.
 * Give each its own space, even if two previews happen to have equal sizes. */
export function storedRasterLayers(prefix:string,label:string,roiId:string,image:string,value:Record<string,unknown>): EvidenceLayer[] {
  const facts={...compactEvidence(value),roi_id:roiId};
  const layer=(id:string,title:string,raster:string,extra:Record<string,unknown>={}): EvidenceLayer=>({
    id:`${prefix}:${id}`,label:title,image:raster,space:`${prefix}:${id}`,facts:{...facts,...extra},
  });
  const layers=[layer('image',label,image)];
  if(typeof value.mask==='string') layers.push(layer('mask',`${label} · 검사 마스크`,value.mask));
  if(typeof value.anomaly_map==='string') layers.push(layer('map',`${label} · 열지도 (${value.map_semantics||'의미 미기록'})`,value.anomaly_map));
  if(Array.isArray(value.segmentation_classes)) for(const raw of value.segmentation_classes) {
    if(!raw || typeof raw!=='object' || Array.isArray(raw)) continue;
    const row=raw as Record<string,unknown>;
    if(typeof row.mask==='string') layers.push(layer(`class:${row.class_id}`,`${label} · ${row.class_name} 마스크`,row.mask,
      {class_id:row.class_id,class_name:row.class_name,area_px:row.area_px}));
  }
  return layers;
}

export function inspectionEvidence(result:FlowchartExecutionResult|null|undefined,identity:Omit<EvidenceView,'layers'>): EvidenceView {
  const size=result?.inspected_image_size?.length===2?result.inspected_image_size as [number,number]:undefined;
  const crops=result?.crops||[];
  return {...identity,
    layers:[...(result?.annotated_image?[{id:'overlay',label:'저장된 판정 overlay',image:result.annotated_image,space:'source',size}]:[]),
      ...crops.flatMap((crop,index)=>storedRasterLayers(`roi:${index}`,`ROI · ${crop.roi_id}`,crop.roi_id,crop.crop_thumbnail,crop as unknown as Record<string,unknown>))],
    boxes:crops.filter(crop=>validBox(crop.bbox,size)).map(crop=>({id:crop.roi_id,box:crop.bbox,space:'source'})),
    facts:{...identity.facts,roi_evidence:crops.map(crop=>compactEvidence(crop as unknown as Record<string,unknown>))},
  };
}
