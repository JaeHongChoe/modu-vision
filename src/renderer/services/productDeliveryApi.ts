import {request} from './api';
export type SavedPackage={package_id:string;name:string;package_path:string;integrity:string;scope_matches:boolean;manifest_sha256?:string;pipeline_id?:string;version_id?:string;recipe_task?:string;created_at?:string;error?:string;approval_present?:boolean;models?:Array<{job_id:string;task:string}>;parity:{status:string};optimization_jobs:Array<{job_id:string;status:string}>};
export type PackageLibrary={packages:SavedPackage[];selected_package_id:string|null;scope:{project_id:string;source_dataset_path:string|null;task:string}};
export type HardwareState={device:string;kind:string;configured:boolean;live_verified:boolean;approved:boolean;evidence:Array<{observed_at:number;manifest_sha256:string;image_sha256:string;verdict:string}>};
export type InstallationState={app_version:string;host:{os:string;architecture:string;python:string};project:{schema_version:number;compatible:boolean;migration_performed:boolean};runtime_dependencies:Record<string,boolean>;python_supported:boolean;sdk:Record<string,{ready:boolean;requires_embedded_python?:boolean;compiler_available?:boolean;python_headers_available?:boolean;dotnet_available?:boolean}>;install:{native_autostart_supported:boolean;signing_verified:boolean};update:{automatic_update_available:boolean;actions:string[]}};
export const productDeliveryApi={
 packages:()=>request<PackageLibrary>('/api/product-delivery/packages'),
 select:(id:string)=>request<SavedPackage>(`/api/product-delivery/packages/${encodeURIComponent(id)}/select`,{method:'POST'}),
 verify:(id:string,image_path:string,device:string)=>request<{result:Record<string,unknown>;evidence:Record<string,unknown>}>(`/api/product-delivery/packages/${encodeURIComponent(id)}/verify`,{method:'POST',body:JSON.stringify({image_path,device})}),
 preflight:(id:string,image_path:string)=>request<{ready:boolean;training_started:false;probe:{checks?:Record<string,unknown>;message?:string};input?:{width:number;height:number;local_sha256:string;remote_sha256:string;execution:string};observed_at:number}>(`/api/product-delivery/servers/${encodeURIComponent(id)}/preflight`,{method:'POST',body:JSON.stringify({image_path})}),
 protocol:(protocol:'http'|'modbus',mode:'success'|'reject'|'timeout')=>request<{acknowledged:boolean;error?:string;scope:string;physical_equipment_verified:false}>('/api/product-delivery/protocol-test',{method:'POST',body:JSON.stringify({protocol,mode})}),
 installation:()=>request<InstallationState>('/api/product-delivery/installation'),
 hardware:()=>request<{devices:HardwareState[]}>('/api/product-delivery/hardware'),
 diagnostics:(sections:Array<'installation'|'packages'|'hardware'|'operator_errors'>=['installation','packages','hardware','operator_errors'])=>request<{filename:string;bundle:Record<string,unknown>;redacted:true}>('/api/product-delivery/diagnostics',{method:'POST',body:JSON.stringify({sections})}),
};
