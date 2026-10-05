import type {PixelEvidence} from '../../services/evaluationEvidence';

export type PixelEvidenceView = 'error' | 'truth' | 'prediction';
type Raster = Pick<ImageData, 'width' | 'height' | 'data'>;

export function pixelEvidencePixels(prediction:Raster, truth:Raster, evidence:PixelEvidence, className:string, view:PixelEvidenceView):Uint8ClampedArray {
  const {width,height}=prediction;
  if (!Number.isInteger(width)||!Number.isInteger(height)||width<=0||height<=0||truth.width!==width||truth.height!==height) {
    throw new Error('정답과 예측 마스크 크기가 다릅니다.');
  }
  if (evidence.shape && (evidence.shape.length!==2||evidence.shape[0]!==height||evidence.shape[1]!==width)) {
    throw new Error('저장된 평가 크기와 마스크 크기가 다릅니다.');
  }
  const id=className==='all'?null:evidence.per_class?.[className]?.class_id;
  if (id!==null&&(typeof id!=='number'||!Number.isInteger(id)||id<0||id>255)) throw new Error('선택 클래스의 저장된 마스크 ID가 없습니다.');
  if (prediction.data.length!==width*height*4||truth.data.length!==width*height*4) throw new Error('마스크 픽셀 크기가 다릅니다.');
  const output=new Uint8ClampedArray(width*height*4);
  for (let i=0;i<output.length;i+=4) {
    for (const raster of [prediction,truth]) {
      if (raster.data[i]!==raster.data[i+1]||raster.data[i]!==raster.data[i+2]||raster.data[i+3]!==255) {
        throw new Error('저장된 클래스 ID 마스크가 필요합니다.');
      }
    }
    const a=truth.data[i],b=prediction.data[i];
    const actual=id===null?a>0:a===id, predicted=id===null?b>0:b===id;
    const color=view==='truth'?(actual?[74,222,128]:[35,35,35]):view==='prediction'?(predicted?[34,211,238]:[35,35,35]):
      [actual&&a!==b?244:35,predicted&&a!==b?180:35,predicted&&a!==b?255:35];
    output.set([...color,255],i);
  }
  return output;
}
