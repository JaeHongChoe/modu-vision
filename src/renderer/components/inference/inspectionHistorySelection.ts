/** An explicit task target must never fall back to a newer inspection. */
export async function openSelectedInspectionRun<T extends {run_id:string}>(
  runs:Array<{run_id:string}>,requestedId:string|undefined,getRun:(id:string)=>Promise<T>,
):Promise<T|null>{
  const id=requestedId||runs[0]?.run_id;
  if(!id)return null;
  if(!runs.some(row=>row.run_id===id))throw new Error(`선택한 검사 ${id}을 현재 소스에서 찾지 못했습니다.`);
  const row=await getRun(id);
  if(row.run_id!==id)throw new Error('조회한 검사 기록의 ID가 선택한 작업과 일치하지 않습니다.');
  return row;
}
