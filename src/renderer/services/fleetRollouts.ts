import {request} from './api';

export type FleetRolloutTarget={target_id:string;target_url:string;status:string;deployment_id:string|null;previous_deployment_id:string|null;rollback_deployment_id?:string;error:string|null;readback:{observed_at:number;runtime?:{status?:string;device?:string;manifest_sha256?:string};matches_active:boolean}|null};
export type FleetRollout={schema_version:1;plan_id:string;revision:number;status:string;operation?:'deploy'|'rollback';release:{manifest_sha256:string;device:string};targets:FleetRolloutTarget[];canary_target_ids:string[];canary_confirmed:boolean;batch_size:number;pause_reason:string|null;updated_at:number;offline_policy:string;events?:{event_id:string;revision:number;event:string;reviewer:string;created_at:number}[]};
export type FleetRolloutInput={package_path:string;device:string;reviewer:string;target_ids:string[];canary_target_ids:string[];batch_size:number};
const endpoint=(id:string)=>`/api/fleet/rollouts/${encodeURIComponent(id)}`;
function action(id:string,name:string,revision:number,reviewer:string,extra:Record<string,unknown>={}){
  return request<FleetRollout>(`${endpoint(id)}/${name}`,{method:'POST',body:JSON.stringify({...extra,expected_revision:revision,reviewer})});
}
export const fleetRollouts={
  list:()=>request<{rollouts:FleetRollout[]}>('/api/fleet/rollouts'),
  read:(id:string)=>request<FleetRollout>(endpoint(id)),
  create:({package_path,device,reviewer,target_ids,canary_target_ids,batch_size}:FleetRolloutInput)=>request<FleetRollout>('/api/fleet/rollouts',{method:'POST',body:JSON.stringify({package_path,device,reviewer,target_ids,canary_target_ids,batch_size})}),
  advance:(id:string,revision:number,reviewer:string,confirmCanary=false)=>action(id,'advance',revision,reviewer,{confirm_canary:confirmCanary}),
  pause:(id:string,revision:number,reviewer:string,reason:string)=>action(id,'pause',revision,reviewer,{reason}),
  resume:(id:string,revision:number,reviewer:string)=>action(id,'resume',revision,reviewer),
  rollback:(id:string,revision:number,reviewer:string)=>action(id,'rollback',revision,reviewer),
};

export function rolloutControls(plan:FleetRollout){
  const terminal=['completed','rolled_back'].includes(plan.status),restoring=plan.operation==='rollback';
  return {advance:!restoring&&['planned','running'].includes(plan.status),
    confirmCanary:!restoring&&plan.status==='waiting_canary_confirmation'&&!plan.canary_confirmed,
    pause:!terminal&&plan.status!=='paused',resume:['paused','running','rolling_back'].includes(plan.status),
    rollback:plan.status!=='rolled_back'&&!(restoring&&plan.status==='paused')&&plan.targets.some(target=>!!target.deployment_id)};
}
