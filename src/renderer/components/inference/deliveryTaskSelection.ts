import type {SavedPackage} from '../../services/productDeliveryApi';
import type {OptimizationJob} from '../../services/runtimeDeploymentApi';
import {packageHandoff} from '../runtime/deliveryContracts';

export async function reopenOptimizationTask(id:string,source:string,api:{
  job:(id:string)=>Promise<OptimizationJob>;packages:()=>Promise<{packages:SavedPackage[]}>;
  select:(id:string)=>Promise<SavedPackage>;
},isCurrent:()=>boolean){
  const job=await api.job(id);
  if(job.job_id!==id)throw new Error('조회한 최적화 기록의 ID가 선택한 작업과 일치하지 않습니다.');
  if(!source||job.options?.input_receipt?.source_dataset_path!==source)throw new Error('최적화 작업의 원본 소스가 현재 프로젝트와 다릅니다.');
  const library=await api.packages();
  const row=library.packages.find(value=>value.package_path===job.options?.package_dir);
  if(!row)throw new Error('이 최적화 작업의 입력 패키지를 보관함에서 찾지 못했습니다.');
  packageHandoff(row);
  if(!isCurrent())return null;
  const selected=await api.select(row.package_id);
  if(selected.package_id!==row.package_id)throw new Error('다시 연 패키지의 ID가 선택한 작업과 일치하지 않습니다.');
  packageHandoff(selected);
  return isCurrent()?{job,package:selected}:null;
}
