import {parseRotatedRows,formatRotatedRows} from './rotatedDirectionRows';
import type {MultiRotatedSample} from '../../services/specializedApi';

export type OCRMode='crop'|'detect_recognize';
export type OCRNormalizer='none'|'strip'|'nfkc'|'nfkc_strip';
export type OBBAdapter='fixed_slot_cnn'|'ultralytics_yolo_obb';

export function ocrRecipePayload(mode:OCRMode,charset:string,normalizer:OCRNormalizer,regex:string,rules:{minLength?:string;maxLength?:string;allowedValues?:string}={}){
  if(new Set(Array.from(charset)).size!==Array.from(charset).length)throw new Error('문자 집합에 중복 문자가 있습니다.');
  if(regex.length>256)throw new Error('문자 규칙 정규식은 256자 이내로 입력하세요.');
  if(regex){try{new RegExp(regex);}catch{throw new Error('문자 규칙 정규식을 확인하세요.');}}
  const text_rules:{regex?:string;min_length?:number;max_length?:number;allowed_values?:string[]}={...(regex?{regex}:{})};
  for(const [input,key] of [[rules.minLength,'min_length'],[rules.maxLength,'max_length']] as const){
    if(input?.trim()){
      const value=Number(input);
      if(!Number.isInteger(value)||value<0||value>10000)throw new Error('문자 길이는 0부터 10000까지의 정수로 입력하세요.');
      text_rules[key]=value;
    }
  }
  if(text_rules.min_length!==undefined&&text_rules.max_length!==undefined&&text_rules.min_length>text_rules.max_length)throw new Error('최소 문자 수는 최대 문자 수 이하여야 합니다.');
  const allowed_values=[...new Set((rules.allowedValues||'').split(/\r?\n/).filter(value=>value.length>0))];
  if(allowed_values.length)text_rules.allowed_values=allowed_values;
  return {mode,charset:charset||null,normalizer,orientation:'horizontal',text_rules};
}

export function formatOCRRate(value:unknown):string{
  return typeof value==='number'&&Number.isFinite(value)&&value>=0?`${(value*100).toFixed(1)}%`:'측정되지 않음';
}

export function ocrRecipeDraft(value:unknown){
  const recipe=(value||{}) as {mode?:OCRMode;charset?:string;normalizer?:OCRNormalizer;text_rules?:{regex?:string;min_length?:number;max_length?:number;allowed_values?:string[]}};
  return {mode:recipe.mode||'crop' as OCRMode,charset:recipe.charset||'',normalizer:recipe.normalizer||'none' as OCRNormalizer,
    regex:recipe.text_rules?.regex||'',minLength:recipe.text_rules?.min_length?.toString()||'',maxLength:recipe.text_rules?.max_length?.toString()||'',allowedValues:recipe.text_rules?.allowed_values?.join('\n')||''};
}

export function ocrTrainingOptions(epochs:number,batch:string,width:string,rate:string){
  const batch_size=Number(batch),image_width=Number(width),learning_rate=Number(rate);
  if(!Number.isInteger(epochs)||epochs<1||epochs>500)throw new Error('OCR epoch는 1부터 500까지의 정수여야 합니다.');
  if(!Number.isInteger(batch_size)||batch_size<1||batch_size>256)throw new Error('OCR batch는 1부터 256까지의 정수여야 합니다.');
  if(!Number.isInteger(image_width)||image_width<8||image_width>4096)throw new Error('OCR 입력 폭은 8부터 4096까지의 정수여야 합니다.');
  if(!Number.isFinite(learning_rate)||learning_rate<=0||learning_rate>1)throw new Error('OCR 학습률은 0보다 크고 1 이하여야 합니다.');
  return {epochs,batch_size,image_width,learning_rate};
}

export function obbRecipePayload(adapter:OBBAdapter,modelPath:string,trustNativeWeights=false){
  const path=modelPath.trim();
  if(adapter==='ultralytics_yolo_obb'&&!(/^(\/|[A-Za-z]:[\\/]|\\\\)/.test(path)))throw new Error('YOLO OBB 모델의 로컬 절대 경로를 입력하세요.');
  if(adapter==='ultralytics_yolo_obb'&&!trustNativeWeights)throw new Error('선택한 로컬 네이티브 모델을 신뢰하는지 명시적으로 확인하세요.');
  return {adapter,angle_convention:'clockwise_degrees_axial_180',direction_schema:'none',empty_background_policy:'explicit_empty_objects',
    ...(adapter==='ultralytics_yolo_obb'?{model_path:path,trust_native_weights:true}:{})};
}

export function parseOBBRows(text:string):MultiRotatedSample[]{
  const lines=text.split(/\r?\n/).filter(line=>line.trim());
  if(!lines.length)throw new Error('회전 박스 또는 빈 정상 정답을 입력하세요.');
  const rows=new Map<string,MultiRotatedSample>();
  for(const line of lines){
    const [image,label,box,split,...extra]=line.split('\t');
    if(!label?.trim()||!box?.trim()){
      if(!image?.trim()||label?.trim()||box?.trim()||extra.some(value=>value.trim())||!['train','val','test'].includes(split))throw new Error('빈 정상 행은 이미지 경로, 빈 라벨, 빈 박스, 분할을 입력하세요.');
      if(rows.has(image.trim()))throw new Error('빈 정상과 객체 정답을 같은 이미지에 함께 지정할 수 없습니다.');
      rows.set(image.trim(),{image:image.trim(),split:split as 'train'|'val'|'test',objects:[]});
    }else{
      const [row]=parseRotatedRows(line);const existing=rows.get(row.image);
      if(existing){if(!existing.objects?.length)throw new Error('빈 정상과 객체 정답을 같은 이미지에 함께 지정할 수 없습니다.');if(existing.split!==row.split)throw new Error('같은 이미지의 객체는 같은 분할을 사용해야 합니다.');existing.objects!.push(...row.objects!);}
      else rows.set(row.image,row);
    }
  }
  return [...rows.values()];
}

export function formatOBBRows(rows:MultiRotatedSample[]):string{
  return rows.map(row=>row.objects?.length===0?`${row.image}\t\t\t${row.split}`:formatRotatedRows([row])).join('\n');
}
