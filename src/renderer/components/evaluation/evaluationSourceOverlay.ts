import type {EvidenceSample,PixelEvidence} from '../../services/evaluationEvidence';
import {safeSnapshot,type EvidenceView} from '../common/evidenceViewer';
import {pixelEvidencePixels,type PixelEvidenceView} from './pixelEvidencePreview';

export interface EvaluationSourcePreview {image:string;original_size:number[];image_sha256:string;image_path:string;evaluation_id:string}

export function projectionSize(evidence:PixelEvidence, preview:EvaluationSourcePreview, expectedHash:string|undefined):[number,number] {
  if (!expectedHash||!/^[a-f0-9]{64}$/.test(expectedHash)||expectedHash!==preview.image_sha256) throw new Error('저장 당시 원본 이미지 해시가 확인되지 않았습니다.');
  const size=evidence.mapping?.source_size;
  if (!['full_image_resize','fixed_input_recipe'].includes(evidence.mapping?.kind||'')||!size||size.length!==2||size.some(value=>!Number.isInteger(value)||value<=0)||
      !preview.original_size||preview.original_size.length!==2||size.some((value,index)=>value!==preview.original_size[index])||
      size[0]*size[1]>20_000_000||!['model_input','model_input_px'].includes(evidence.coordinate_space||'')) {
    throw new Error('저장된 원본 좌표 변환이 없어 마스크를 겹칠 수 없습니다.');
  }
  if(evidence.mapping?.kind==='fixed_input_recipe'){
    recipeGeometry(evidence.mapping);
    const prepared=evidence.mapping.geometry!.prepared_size;
    if(!evidence.shape||evidence.shape.length!==2||evidence.shape[0]!==prepared[1]||evidence.shape[1]!==prepared[0])throw new Error('저장된 마스크와 입력 좌표 크기가 다릅니다.');
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

type Mapping=NonNullable<PixelEvidence['mapping']>;
const validSize=(size:unknown):size is [number,number]=>Array.isArray(size)&&size.length===2&&size.every(n=>Number.isSafeInteger(n)&&n>0)&&size[0]*size[1]<=20_000_000;
function recipeGeometry(mapping:Mapping):NonNullable<Mapping['geometry']>{
  const geometry=mapping.geometry,fail=()=>{throw new Error('저장된 고정 입력 좌표 변환이 올바르지 않습니다.');};
  if(mapping.kind!=='fixed_input_recipe'||!mapping.input_recipe_sha256||!/^[a-f0-9]{64}$/.test(mapping.input_recipe_sha256)||!geometry||!validSize(geometry.source_size)||!validSize(geometry.prepared_size)||!Array.isArray(geometry.steps)||geometry.steps.length>17||geometry.source_size.some((n,i)=>n!==mapping.source_size[i]))return fail();
  let current=[...geometry.source_size];
  geometry.steps.forEach((step,index)=>{
    if(!validSize(step.before)||step.before.some((n,i)=>n!==current[i]))return fail();
    if(step.op==='crop'){
      const box=step.box;if(!box||box.length!==4||box.some(n=>!Number.isSafeInteger(n))||box[0]<0||box[1]<0||box[0]>=box[2]||box[1]>=box[3]||box[2]>current[0]||box[3]>current[1])return fail();
      current=[box[2]-box[0],box[3]-box[1]];
    }else if(step.op==='rotate90'){
      if(!Number.isInteger(step.turns)||step.turns!<1||step.turns!>3)return fail();
      if(step.turns!%2)current=[current[1],current[0]];
    }else if(step.op==='resize'){
      const after=geometry.steps[index+1]?.before??geometry.prepared_size;if(!validSize(after))return fail();current=[...after];
    }else if(step.op!=='brightness'&&step.op!=='contrast')return fail();
  });
  if(current.some((n,i)=>n!==geometry.prepared_size[i]))return fail();
  return geometry;
}
/** Reverse only the recorded spatial operations. Excluded source pixels stay transparent. */
export function projectRecipePixels(data:Uint8ClampedArray,width:number,height:number,mapping:Mapping):Uint8ClampedArray {
  const geometry=recipeGeometry(mapping);
  if(width!==geometry.prepared_size[0]||height!==geometry.prepared_size[1]||data.length!==width*height*4)throw new Error('저장된 마스크와 입력 좌표 크기가 다릅니다.');
  let result:Uint8ClampedArray=data.slice(),w=width,h=height;
  for(const step of [...geometry.steps].reverse()){
    const [bw,bh]=step.before;
    if(step.op==='resize')result=projectedPixels(result,w,h,bw,bh);
    else if(step.op==='crop'){
      const [x1,y1,x2,y2]=step.box!;
      if(w!==x2-x1||h!==y2-y1)throw new Error('저장된 자르기 좌표 크기가 다릅니다.');
      const canvas=new Uint8ClampedArray(bw*bh*4);
      for(let y=0;y<h;y++)canvas.set(result.subarray(y*w*4,(y+1)*w*4),((y+y1)*bw+x1)*4);
      result=canvas;
    }else if(step.op==='rotate90'){
      const canvas=new Uint8ClampedArray(bw*bh*4),turns=step.turns!;
      for(let y=0;y<bh;y++)for(let x=0;x<bw;x++){
        const sx=turns===1?y:turns===2?bw-1-x:bh-1-y,sy=turns===1?bw-1-x:turns===2?bh-1-y:x;
        canvas.set(result.subarray((sy*w+sx)*4,(sy*w+sx+1)*4),(y*bw+x)*4);
      }
      result=canvas;
    }
    w=bw;h=bh;
  }
  // Background has no overlay color; preserve every foreground pixel exactly.
  for(let i=0;i<result.length;i+=4)if(result[i]===35&&result[i+1]===35&&result[i+2]===35)result[i+3]=0;
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
    const pixels=target.createImageData(output.width,output.height);const sourceRaster=evidence.mapping?.kind==='fixed_input_recipe'?projectRecipePixels(raster,p.width,p.height,evidence.mapping):raster;
    pixels.data.set(projectedPixels(sourceRaster,evidence.mapping?.kind==='fixed_input_recipe'?size[0]:p.width,evidence.mapping?.kind==='fixed_input_recipe'?size[1]:p.height,output.width,output.height));target.putImageData(pixels,0,0);
    layers.push({id:view,label,image:output.toDataURL('image/png'),space:'source',size});
  }
  return {key:`${preview.evaluation_id}:${row.file_path}:${className}`,title:'평가 원본과 저장 마스크',versionId:preview.evaluation_id,imagePath:row.file_path,imageSha256:row.image_sha256,layers,
    facts:{className,coordinate_mapping:evidence.mapping,model_input_shape:evidence.shape,projection:'nearest floor sampling for display only',saved_metrics_unchanged:true},warning:evidence.mapping?.kind==='fixed_input_recipe'?'저장된 자르기·회전·크기 변환을 되돌려 표시한 마스크입니다. 제외 영역은 표시하지 않으며 저장 지표는 바뀌지 않습니다.':'전체 이미지 크기 변환으로 표시한 마스크입니다. 원본 해상도의 정답이나 측정값을 새로 산출하지 않습니다.'};
}
