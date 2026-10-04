import {request} from './api';
import type {FlowExecutionResources} from '../types';
import type {CalibrationRecord,KnownLengthSegment} from '../components/flowchart/spatialCalibration';
export interface FixtureRecord {ref:string;revision:number;name:string;source_sha256:string;image_size:[number,number];valid_region:number[];fixture_id:string}
export const geometryFlowApi={
  fixturePreview:(ref:string)=>request<{reference_ref:string;source_size:[number,number];preview_data_url:string}>(`/api/geometry/fixtures/${encodeURIComponent(ref)}/preview`),
  fixtures:()=>request<{fixtures:FixtureRecord[]}>("/api/geometry/fixtures"),
  createFixture:(body:{image_path:string;name:string;valid_region:number[]})=>request<FixtureRecord>("/api/geometry/fixtures",{method:"POST",body:JSON.stringify(body)}),
  source:(image_path:string)=>request<{source_size:[number,number];source_sha256:string;preview_data_url:string}>(`/api/geometry/source-preview?image_path=${encodeURIComponent(image_path)}`),
  resources:()=>request<FlowExecutionResources>('/api/flowchart/execution-resources?device=cpu'),
  calibrations:()=>request<{calibrations:CalibrationRecord[]}>('/api/geometry/calibrations'),
  createKnownLengthCalibration:(body:{camera_id:string;acquisition_config:Record<string,string|number>;source_size:number[];segments:KnownLengthSegment[];tolerance_mm:number;isotropic:boolean})=>
    request<CalibrationRecord>('/api/geometry/calibrations/known-lengths',{method:'POST',body:JSON.stringify(body)}),
  configureCPU:(device_slots:number)=>request<FlowExecutionResources>('/api/flowchart/execution-resources',{method:'PUT',body:JSON.stringify({device:'cpu',device_slots})}),
};
