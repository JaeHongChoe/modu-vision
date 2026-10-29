export type FixedInputOperation =
  | {op:'crop';roi:[number,number,number,number]}
  | {op:'rotate90';turns:number}
  | {op:'brightness'|'contrast';factor:number}
  | {op:'resize';width:number;height:number};
export type FixedInputRecipe = {version:1;operations:FixedInputOperation[]};
export type InputRecipeScope = {apiIdentity:string;projectId:string;labelsetId:string;source:string;task:string};
export const inputPreparationScope=(scope:InputRecipeScope)=>JSON.stringify([scope.apiIdentity,scope.projectId,scope.labelsetId,scope.source,scope.task]);
export const supportsInputPreparation=(task:string)=>task==='classification'||task==='segmentation';
export function validateInputRecipe(value:FixedInputRecipe,task:string):FixedInputRecipe {
  if(value.version!==1||!Array.isArray(value.operations)||value.operations.length>16)throw new Error('규칙 버전과 연산 수를 확인하세요. 최대 16개까지 사용할 수 있습니다.');
  if(value.operations.length&&!supportsInputPreparation(task))throw new Error('고정 입력 규칙은 현재 분류·영역 분할에서 지원합니다. 다른 모델군은 기존 입력을 사용하세요.');
  for(const row of value.operations){
    if(row.op==='crop'){
      if(row.roi.length!==4||row.roi.some(n=>!Number.isFinite(n)||n<0||n>1)||row.roi[0]>=row.roi[2]||row.roi[1]>=row.roi[3])throw new Error('ROI는 0~1 사이의 왼쪽·위·오른쪽·아래 좌표로 입력하세요.');
    }else if(row.op==='rotate90'){
      if(!Number.isInteger(row.turns)||row.turns<1||row.turns>3)throw new Error('회전은 90°·180°·270° 중에서 고르세요.');
    }else if(row.op==='resize'){
      if([row.width,row.height].some(n=>!Number.isInteger(n)||n<16||n>4096))throw new Error('입력 크기는 16~4096 정수로 지정하세요.');
    }else if(row.op==='brightness'||row.op==='contrast'){
      if(!Number.isFinite(row.factor)||row.factor<.25||row.factor>4)throw new Error('밝기·대비 배율은 0.25~4 사이로 지정하세요.');
    }else throw new Error('지원하지 않는 고정 입력 연산입니다.');
  }
  return {version:1,operations:value.operations.map(row=>({...row,...(row.op==='crop'?{roi:[...row.roi] as [number,number,number,number]}:{})}))};
}
export function moveInputOperation(rows:FixedInputOperation[],index:number,delta:number):FixedInputOperation[]{
  const destination=index+delta;if(destination<0||destination>=rows.length)return rows;
  const next=[...rows];[next[index],next[destination]]=[next[destination],next[index]];return next;
}
