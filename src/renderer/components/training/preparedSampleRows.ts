/** Only human-entered truth and a gallery-selected source are serialized. */
export function projectSampleRow(source:string,path:string,truth:string,split:string):string {
  const prefix=source.replace(/\/+$/,'')+'/';
  if(!path.startsWith(prefix))throw new Error('현재 프로젝트 출처의 이미지를 선택하세요.');
  const relative=path.slice(prefix.length);
  if(!relative||relative.split('/').some(part=>part==='..'||part==='.')||/[\r\n\t]/.test(relative))throw new Error('이미지 출처 경로를 확인하세요.');
  if(!truth.trim()||/[\r\n\t]/.test(truth)||!['train','val','test'].includes(split))throw new Error('정답과 이미지 분할을 확인하세요.');
  return `${relative}\t${truth.trim()}\t${split}`;
}
export function replaceSampleRow(rows:string,row:string):string {
  const image=row.split('\t')[0];return [...rows.split(/\r?\n/).filter(old=>old&&old.split('\t')[0]!==image),row].join('\n');
}
export function annotationCrops(annotations:Array<{type:string;label:string;bbox?:number[];polygon?:number[][];points?:number[][]}>,width:number,height:number):Array<{label:string;bbox:[number,number,number,number]}> {
  if(!(width>0&&height>0))throw new Error('원본 이미지 크기를 확인하세요.');
  return annotations.filter(row=>row.type==='bbox'||row.type==='polygon').map(row=>{
    const points=row.polygon||row.points;const coordinates=row.bbox||(points?.length?[
      Math.floor(Math.min(...points.map(point=>point[0]))),Math.floor(Math.min(...points.map(point=>point[1]))),
      Math.ceil(Math.max(...points.map(point=>point[0]))),Math.ceil(Math.max(...points.map(point=>point[1])))]:[]);
    if(coordinates.length!==4||coordinates.some(value=>!Number.isFinite(value)||!Number.isInteger(value))||!row.label.trim()||/[\t\r\n]/.test(row.label))throw new Error('결함 영역 좌표와 정답 라벨을 확인하세요.');
    const [x0,y0,x1,y1]=coordinates;
    if(x0<0||y0<0||x1>width||y1>height||x1-x0<8||y1-y0<8)throw new Error('결함 crop은 원본 이미지 안의 8px 이상 영역이어야 합니다.');
    return {label:row.label,bbox:[x0,y0,x1,y1]};
  });
}
