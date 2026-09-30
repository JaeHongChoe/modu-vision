import {request} from './api';
import type {FlowExecutionResources} from '../types';
export const geometryFlowApi={
  source:(image_path:string)=>request<{source_size:[number,number];source_sha256:string;preview_data_url:string}>(`/api/geometry/source-preview?image_path=${encodeURIComponent(image_path)}`),
  resources:()=>request<FlowExecutionResources>('/api/flowchart/execution-resources?device=cpu'),
  configureCPU:(device_slots:number)=>request<FlowExecutionResources>('/api/flowchart/execution-resources',{method:'PUT',body:JSON.stringify({device:'cpu',device_slots})}),
};
