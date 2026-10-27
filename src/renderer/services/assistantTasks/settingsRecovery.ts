/** Only restores ephemeral local training inputs after a reload, never project/source/compute authority. */
export function trainingSettingsRecovery(saved:string,current:string):{device:string|null;batch:number|null;label:string}|null{
  try{
    const target=JSON.parse(saved),now=JSON.parse(current);
    if(!Array.isArray(target)||!Array.isArray(now)||target.length!==9||now.length!==9
      ||JSON.stringify(target.slice(0,7))!==JSON.stringify(now.slice(0,7)))return null;
    const device=target[7],batch=target[8];
    if(!(device===null||['auto','cpu','mps','cuda'].includes(device))
      ||!(batch===null||Number.isInteger(batch)&&batch>=1&&batch<=128))return null;
    // Existing nondefault edits are a person's current choice; never replace them through recovery.
    if((now[7]!==null&&now[7]!==device)||(now[8]!==null&&now[8]!==batch)
      ||(now[7]===device&&now[8]===batch))return null;
    return {device,batch,label:`저장 설정 복원 · ${device?.toUpperCase()||'자동 장치'} · ${batch===null?'기본 배치':`배치 ${batch}`}`};
  }catch{return null;}
}
