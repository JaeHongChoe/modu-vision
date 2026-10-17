export interface ErrorCounts {tp:number;fp:number;fn:number;precision?:number;recall?:number;f1?:number;class_id?:number;predicted_area_px?:number;truth_area_px?:number}
export interface EvidenceBox {label:string;confidence?:number;box:number[]|{cx:number;cy:number;width:number;height:number;angle_deg:number}}
export interface ObjectEvidence {predicted:EvidenceBox[];truth:EvidenceBox[];per_class:Record<string,ErrorCounts>;matches:{prediction_index:number;truth_index:number;label:string;iou:number;angle_error_deg?:number}[];missing_truth_indices:number[];extra_prediction_indices:number[];source_size?:number[];coordinate_space?:string}
export interface PixelEvidence {per_class?:Record<string,ErrorCounts>;coordinate_space?:string;shape?:number[];prediction_mask?:string;truth_mask?:string;mapping?:{kind:string;source_size:number[]}}
export interface CharacterEvidence {per_character:Record<string,ErrorCounts>;edit_distance:number;alignment:{operation:string;reference:string|null;predicted:string|null;reference_index:number|null;prediction_index:number|null}[]}
export interface EvidenceSample {file_path?:string;file_name?:string;image?:string;image_sha256?:string;ground_truth?:string;predicted_class?:string;confidence?:number;defect_score?:number;class_scores?:Record<string,number>;object_evidence?:ObjectEvidence;pixel_evidence?:PixelEvidence;character_evidence?:CharacterEvidence;is_correct?:boolean;oriented_iou?:number;angle_error_deg?:number}
/** A saved display may refuse derived controls while retaining its original JSON. */
export function savedEvidenceIssue(rows:unknown):string|null{
  const countsIssue='저장된 클래스별 TP·FN·FP 지표가 유효하지 않아 클래스 지표와 필터를 표시할 수 없습니다.';
  const scoreIssue='저장된 평가 점수가 유효하지 않아 클래스 지표와 필터를 표시할 수 없습니다.';
  const areaIssue='저장된 객체 좌표나 결함 면적이 유효하지 않아 분포와 필터를 표시할 수 없습니다.';
  const object=(value:unknown):value is Record<string,unknown>=>!!value&&typeof value==='object'&&!Array.isArray(value);
  const score=(value:unknown)=>value===null||value===undefined||(typeof value==='number'&&Number.isFinite(value));
  const finite=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value);
  if(!Array.isArray(rows))return '저장된 이미지별 평가 증거가 유효하지 않아 클래스 지표와 필터를 표시할 수 없습니다.';
  for(const row of rows){
    if(!object(row))return '저장된 이미지별 평가 증거가 유효하지 않아 클래스 지표와 필터를 표시할 수 없습니다.';
    for(const [family,key] of [['object_evidence','per_class'],['pixel_evidence','per_class'],['character_evidence','per_character']]){
      const evidence=row[family];if(evidence===null||evidence===undefined)continue;if(!object(evidence))return countsIssue;
      const table=evidence[key];if(table===null||table===undefined)continue;if(!object(table))return countsIssue;
      for(const value of Object.values(table))if(!object(value)||['tp','fn','fp'].some(key=>value[key]!==null&&value[key]!==undefined&&(typeof value[key]!=='number'||!Number.isSafeInteger(value[key])||(value[key] as number)<0)))return countsIssue;
    }
    if(!score(row.defect_score)||!score(row.confidence))return scoreIssue;
    if(row.class_scores!==null&&row.class_scores!==undefined&&(!object(row.class_scores)||Object.values(row.class_scores).some(value=>!score(value))))return scoreIssue;
    const predicted=object(row.object_evidence)?row.object_evidence.predicted:undefined;
    if(predicted!==null&&predicted!==undefined){
      if(!Array.isArray(predicted))return areaIssue;
      for(const item of predicted){
        if(!object(item))return areaIssue;
        const box=item.box;
        if(Array.isArray(box)){
          if(box.length!==4||!box.every(finite)||box[2]<box[0]||box[3]<box[1]||!Number.isFinite((box[2]-box[0])*(box[3]-box[1])))return areaIssue;
        }else if(!object(box)||!['cx','cy','width','height','angle_deg'].every(key=>finite(box[key]))||(box.width as number)<0||(box.height as number)<0||!Number.isFinite((box.width as number)*(box.height as number)))return areaIssue;
      }
    }
    const pixels=object(row.pixel_evidence)&&object(row.pixel_evidence.per_class)?row.pixel_evidence.per_class:undefined;
    if(pixels)for(const value of Object.values(pixels)){
      if(!object(value))return areaIssue;
      const area=value.predicted_area_px;
      if(area!==null&&area!==undefined&&(!finite(area)||area<0))return areaIssue;
    }
  }
  return null;
}
export function evidenceClasses(rows:EvidenceSample[]):string[]{return [...new Set(rows.flatMap(r=>Object.keys(r.object_evidence?.per_class||{}).concat(Object.keys(r.pixel_evidence?.per_class||{}),Object.keys(r.character_evidence?.per_character||{}),Object.keys(r.class_scores||{}))))].sort();}
export function sampleCounts(row:EvidenceSample,name:string):ErrorCounts{
  const values=[row.object_evidence?.per_class,row.pixel_evidence?.per_class,row.character_evidence?.per_character];
  return values.flatMap(v=>name==='all'?Object.values(v||{}):v?.[name]?[v[name]]:[]).reduce((a,b)=>({tp:a.tp+b.tp,fp:a.fp+b.fp,fn:a.fn+b.fn}),{tp:0,fp:0,fn:0});
}
export function sampleScore(row:EvidenceSample,name:string){return name!=='all'&&row.class_scores?.[name]!==undefined?row.class_scores[name]:row.defect_score??row.confidence;}
export function filterEvidence(rows:EvidenceSample[],name:string,error:string,scoreRange:[number,number]|null):EvidenceSample[]{return rows.filter(r=>{const c=sampleCounts(r,name);const score=sampleScore(r,name);return (error==='all'||(error==='fn'&&c.fn>0)||(error==='fp'&&c.fp>0)||(error==='incorrect'&&r.is_correct===false))&&(!scoreRange||(score!==undefined&&score>=scoreRange[0]&&(scoreRange[1]===1?score<=1:score<scoreRange[1])));});}
export function scoreHistogram(rows:EvidenceSample[],name:string){return Array.from({length:10},(_,i)=>({lower:i/10,upper:(i+1)/10,count:rows.filter(r=>{const s=sampleScore(r,name);return s!==undefined&&s>=i/10&&(i===9?s<=1:s<(i+1)/10);}).length}));}
export function rocEvidence(rows:EvidenceSample[]){
  const data=binarySamples(rows);const positive=data.filter(r=>r.positive).length;const negative=data.length-positive;
  if(!positive||!negative)return {available:false,auc:null,points:[] as {threshold:number;tpr:number;fpr:number}[]};
  const points=[1.0000001,...Array.from(new Set(data.map(r=>r.score))).sort((a,b)=>b-a),0].map(threshold=>({threshold,tpr:data.filter(r=>r.positive&&r.score>=threshold).length/positive,fpr:data.filter(r=>!r.positive&&r.score>=threshold).length/negative}));
  const auc=points.slice(1).reduce((sum,p,i)=>sum+(p.fpr-points[i].fpr)*(p.tpr+points[i].tpr)/2,0);return {available:true,auc,points};
}
export interface AreaItem {path:string;name:string;area:number;unit:'original_image_px2'|'model_input_px2'}
export function areaHistograms(rows:EvidenceSample[],name:string){
  const items:AreaItem[]=rows.flatMap(row=>Object.entries(row.pixel_evidence?.per_class||{}).filter(([label,c])=>(name==='all'||label===name)&&(c.class_id||0)>0&&Number.isFinite(c.predicted_area_px)).map<AreaItem>(([label,c])=>({path:row.file_path||'',name:label,area:c.predicted_area_px!,unit:'model_input_px2' as const})).concat((row.object_evidence?.predicted||[]).filter(b=>name==='all'||b.label===name).map(b=>({path:row.file_path||'',name:b.label,area:Array.isArray(b.box)?Math.max(0,b.box[2]-b.box[0])*Math.max(0,b.box[3]-b.box[1]):b.box.width*b.box.height,unit:'original_image_px2' as const}))));
  return (['original_image_px2','model_input_px2'] as const).filter(unit=>items.some(i=>i.unit===unit)).map(unit=>{const values=items.filter(i=>i.unit===unit);const top=Math.max(1,...values.map(i=>i.area));const step=top/10;return {unit,bins:Array.from({length:10},(_,i)=>({lower:i*step,upper:(i+1)*step,items:values.filter(v=>v.area>=i*step&&(i===9?v.area<=top:v.area<(i+1)*step))}))};});
}
function binarySamples(rows:EvidenceSample[]){
  const normal=new Set(['ok','normal','pass','good','background','정상','양품','합격','0','ok_normal','true_ok','ok_chip']);
  return rows.filter(r=>r.defect_score!==undefined&&Number.isFinite(r.defect_score)&&!!r.ground_truth&&!['unknown','review'].includes(r.ground_truth.toLowerCase())).map(r=>{const value=r.ground_truth!.trim().toLowerCase().replace(/-/g,'_');const defect=value.split('_').some(v=>['ng','defect','fail'].includes(v));return {row:r,score:r.defect_score!,positive:defect||!(normal.has(value)||/^(ok|normal|good)_/.test(value)||/_(ok|normal|good)$/.test(value))};});
}
export function thresholdEvidence(rows:EvidenceSample[],threshold:number){const data=binarySamples(rows);return {tp:data.filter(d=>d.positive&&d.score>=threshold).length,fn:data.filter(d=>d.positive&&d.score<threshold).length,fp:data.filter(d=>!d.positive&&d.score>=threshold).length,tn:data.filter(d=>!d.positive&&d.score<threshold).length};}
