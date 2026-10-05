import type {EvidenceSample,PixelEvidence} from '../../services/evaluationEvidence';
import {safeSnapshot,type EvidenceView} from '../common/evidenceViewer';
import {pixelEvidencePixels,type PixelEvidenceView} from './pixelEvidencePreview';

export interface EvaluationSourcePreview {image:string;original_size:number[];image_sha256:string;image_path:string;evaluation_id:string}

export function projectionSize(evidence:PixelEvidence, preview:EvaluationSourcePreview, expectedHash:string|undefined):[number,number] {
  if (!expectedHash||!/^[a-f0-9]{64}$/.test(expectedHash)||expectedHash!==preview.image_sha256) throw new Error('저장 당시 원본 이미지 해시가 확인되지 않았습니다.');
  const size=evidence.mapping?.source_size;
  if (evidence.mapping?.kind!=='full_image_resize'||!size||size.length!==2||size.some(value=>!Number.isInteger(value)||value<=0)||
      !preview.original_size||preview.original_size.length!==2||size.some((value,index)=>value!==preview.original_size[index])||
      size[0]*size[1]>20_000_000||!['model_input','model_input_px'].includes(evidence.coordinate_space||'')) {
    throw new Error('저장된 원본 좌표 변환이 없어 마스크를 겹칠 수 없습니다.');
  }
  return size as [number,number];
}

/** Display projection only. It does not reconstruct original-resolution truth or change saved metrics. */
export function projectedPixels(data:Uint8ClampedArray,width:number,height:number,outWidth:number,outHeight:number):Uint8ClampedArray {
  if (![width,height,outWidth,outHeight].every(value=>Number.isInteger(value)&&value>0)||data.length!==width*height*4||outWidth*outHeight>20_000_000) throw new Error('평가 마스크 표시 크기가 올바르지 않습니다.');
  const result=new Uint8ClampedArray(outWidth*outHeight*4);
  for(let y=0;y<outHeight;y++)for(let x=0;x<outWidth;x++){
    const source=(Math.floor(y*height/outHeight)*width+Math.floor(x*width/outWidth))*4,target=(y*outWidth+x)*4;
    result.set(data.subarray(source,source+4),target);
    if(data[source]===35&&data[source+1]===35&&data[source+2]===35)result[target+3]=0;
  }
  return result;
}

async function image(src:string):Promise<HTMLImageElement>{
  if(!safeSnapshot(src))throw new Error('검증된 저장 이미지가 필요합니다.');
  return new Promise((resolve,reject)=>{const value=new Image();value.onload=()=>resolve(value);value.onerror=()=>reject(new Error('저장된 평가 이미지를 읽지 못했습니다.'));value.src=src;});
}

export async function evaluationSourceView(row:EvidenceSample,className:string,preview:EvaluationSourcePreview):Promise<EvidenceView>{
  const evidence=row.pixel_evidence;if(!evidence?.truth_mask||!evidence.prediction_mask)throw new Error('정답과 예측 마스크가 모두 필요합니다.');
  const size=projectionSize(evidence,preview,row.image_sha256);
  if(preview.image_path!==row.file_path)throw new Error('평가 원본 이미지 선택이 변경됐습니다.');
  const [original,prediction,truth]=await Promise.all([image(preview.image),image(evidence.prediction_mask),image(evidence.truth_mask)]);
  if(prediction.width!==truth.width||prediction.height!==truth.height)throw new Error('정답과 예측 마스크 크기가 다릅니다.');
  if(original.width>size[0]||original.height>size[1]||Math.abs(original.width/original.height-size[0]/size[1])>1/Math.min(original.width,original.height))throw new Error('평가 원본 미리보기 좌표가 다릅니다.');
  const canvas=document.createElement('canvas');canvas.width=prediction.width;canvas.height=prediction.height;const ctx=canvas.getContext('2d');if(!ctx)throw new Error('마스크 표시 공간을 만들지 못했습니다.');
  ctx.drawImage(prediction,0,0);const p=ctx.getImageData(0,0,canvas.width,canvas.height);ctx.drawImage(truth,0,0);const t=ctx.getImageData(0,0,canvas.width,canvas.height);
  const layers=([{id:'original',label:'평가 입력 원본',image:preview.image,space:'source',size}]);
  for(const [view,label]of [['truth','정답 마스크'],['prediction','예측 마스크'],['error','미검·과검 마스크']] as Array<[PixelEvidenceView,string]>){
    const raster=pixelEvidencePixels(p,t,evidence,className,view);const output=document.createElement('canvas');output.width=original.width;output.height=original.height;const target=output.getContext('2d');if(!target)throw new Error('원본 마스크 표시 공간을 만들지 못했습니다.');
    const pixels=target.createImageData(output.width,output.height);pixels.data.set(projectedPixels(raster,p.width,p.height,output.width,output.height));target.putImageData(pixels,0,0);
    layers.push({id:view,label,image:output.toDataURL('image/png'),space:'source',size});
  }
  return {key:`${preview.evaluation_id}:${row.file_path}:${className}`,title:'평가 원본과 저장 마스크',versionId:preview.evaluation_id,imagePath:row.file_path,imageSha256:row.image_sha256,layers,
    facts:{className,coordinate_mapping:evidence.mapping,model_input_shape:evidence.shape,projection:'nearest floor sampling for display only',saved_metrics_unchanged:true},warning:'전체 이미지 크기 변환으로 표시한 마스크입니다. 원본 해상도의 정답이나 측정값을 새로 산출하지 않습니다.'};
}
