import type {ExplicitGANRow,GANRegion} from '../../services/ganWorkflow';
export function parseGANCrops(value:string):ExplicitGANRow[] {
  const lines=value.split(/\r?\n/).map(line=>line.trim()).filter(Boolean);
  if(!lines.length)throw new Error('결함 이미지와 영역을 입력하세요.');
  return lines.map((line,index)=>{
    const [image,bboxText,split,label,...extra]=line.split('\t');const bbox=bboxText?.split(',').map(Number)||[];
    if(!image?.trim()||extra.length||!['train','val','test'].includes(split)||bbox.length!==4||bbox.some(n=>!Number.isInteger(n))||bbox[0]<0||bbox[1]<0||bbox[2]-bbox[0]<16||bbox[3]-bbox[1]<16)throw new Error(`${index+1}행은 이미지 상대 경로, x1,y1,x2,y2, train/val/test, 선택 라벨을 탭으로 나누세요. 영역은 16×16px 이상이어야 합니다.`);
    return {image:image.trim(),bbox:bbox as [number,number,number,number],split:split as ExplicitGANRow['split'],...(label?.trim()?{label:label.trim()}:{})};
  });
}
export function validateGANRegions(regions:GANRegion[],size:number[]):string|null {
  if(!regions.length||regions.length>32)return '합성 영역을 1~32개 지정하세요.';
  const ids=new Set();
  for(const row of regions){
    const [x1,y1,x2,y2]=row.bbox;
    if(!row.id.trim()||ids.has(row.id)||row.bbox.length!==4||row.bbox.some(v=>!Number.isInteger(v))||x1<0||y1<0||x2<=x1||y2<=y1||x2>size[0]||y2>size[1]||!Number.isFinite(row.opacity)||row.opacity<=0||row.opacity>1||!Number.isInteger(row.feather_px)||row.feather_px<0||row.feather_px>1024)return '영역 이름, 원본 좌표, 불투명도와 경계 폭을 확인하세요.';
    ids.add(row.id);
    if(row.mask_polygon&&(row.mask_polygon.length<3||row.mask_polygon.some(p=>p.length!==2||p.some(v=>!Number.isFinite(v))||p[0]<x1||p[0]>x2||p[1]<y1||p[1]>y2)))return '외곽선의 세 점 이상을 영역 안에 지정하세요.';
  }return null;
}
