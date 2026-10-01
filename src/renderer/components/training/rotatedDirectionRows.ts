import type {MultiRotatedSample} from '../../services/specializedApi';
export function parseRotatedRows(text:string):MultiRotatedSample[]{
  const lines=text.split(/\r?\n/).map(line=>line.trim()).filter(Boolean);
  if(!lines.length)throw new Error('회전 박스 정답을 한 줄 이상 입력하세요.');
  const groups=new Map<string,MultiRotatedSample>();
  lines.forEach((line,index)=>{
    const [image,label,coordinates,split,direction,...extra]=line.split('\t');const values=coordinates?.split(',').map(Number)||[];
    if(!image?.trim()||!label?.trim()||extra.length||values.length!==5||values.some(v=>!Number.isFinite(v))||!['train','val','test'].includes(split))throw new Error(`${index+1}행은 이미지 경로, 라벨, cx,cy,너비,높이,축 각도, 분할, 선택 방향을 탭으로 나누세요.`);
    const [cx,cy,width,height,angle_deg]=values;
    if(width<=0||height<=0||angle_deg< -90||angle_deg>=90)throw new Error(`${index+1}행의 너비·높이는 양수, 축 각도는 -90° 이상 90° 미만이어야 합니다.`);
    const direction_deg=direction?.trim()?Number(direction):undefined;
    if(direction_deg!==undefined&&(!Number.isFinite(direction_deg)||direction_deg<0||direction_deg>=360))throw new Error(`${index+1}행의 객체 방향은 0° 이상 360° 미만이어야 합니다.`);
    const object={label:label.trim(),box:{cx,cy,width,height,angle_deg},...(direction_deg===undefined?{}:{direction_deg})};const existing=groups.get(image);
    if(existing&&existing.split!==split)throw new Error('같은 이미지의 객체는 같은 분할을 사용해야 합니다.');
    if(existing)existing.objects!.push(object);else groups.set(image,{image:image.trim(),split:split as 'train'|'val'|'test',objects:[object]});
  });return [...groups.values()];
}
export function formatRotatedRows(rows:MultiRotatedSample[]):string{
  return rows.flatMap(row=>(row.objects||[{label:row.label!,box:row.box!,direction_deg:row.direction_deg}]).map(obj=>`${row.image}\t${obj.label}\t${[obj.box.cx,obj.box.cy,obj.box.width,obj.box.height,obj.box.angle_deg].join(',')}\t${row.split}${obj.direction_deg===undefined?'':`\t${obj.direction_deg}`}`)).join('\n');
}
