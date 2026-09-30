import {request} from './api';
import type {ImageMeta} from '../types';
export interface ProjectPreferences {
  schema_version:1;revision:number;tag_colors:Record<string,string>;
  model_flags:Record<string,string[]>;labelset_flags:Record<string,string[]>;
}
export type DatasetPartition='all'|'train'|'val'|'test'|'not_used'|'not_split';
export interface DatasetStatistics {
  total:number;labelset_id:string;labeling:Record<'labeled'|'unlabeled',{count:number;ratio:number}>;
  assignments:Record<Exclude<DatasetPartition,'all'>,{count:number;ratio:number}>;
  classes:Record<string,{count:number;ratio:number}>;items:ImageMeta[];
}
export const projectPreferences={
  read:()=>request<ProjectPreferences>('/api/project/preferences'),
  update:(revision:number,actor:string,changes:Partial<Omit<ProjectPreferences,'schema_version'|'revision'>>)=>request<ProjectPreferences>('/api/project/preferences',{method:'PATCH',body:JSON.stringify({expected_revision:revision,actor,changes})}),
  statistics:()=>request<DatasetStatistics>('/api/dataset/metadata/statistics'),
};
