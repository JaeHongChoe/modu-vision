export interface PairMapping {input:string;target:string;split:'train'|'val'|'test'}
export function parseEnhancementPairs(text:string):PairMapping[]{
 const lines=text.split(/\r?\n/).map(line=>line.trim()).filter(Boolean);
 if(lines.length<3||lines.length>10000)throw Error('train/val/test를 포함한 3–10000개 정답 쌍을 입력하세요.');
 return lines.map((line,index)=>{
  const columns=line.split('\t').map(value=>value.trim());
  if(columns.length!==3||!columns[0]||!columns[1]||!['train','val','test'].includes(columns[2]))throw Error(`${index+1}행: 입력 상대 경로, 정답 상대 경로, train/val/test를 탭으로 구분하세요.`);
  return {input:columns[0],target:columns[1],split:columns[2] as PairMapping['split']};
 });
}
