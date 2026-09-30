import React,{useEffect,useState} from 'react';
import {RefreshCw} from 'lucide-react';
import {projectPreferences,type DatasetStatistics,type DatasetPartition} from '../../services/projectPreferences';
import {useDatasetStore} from '../../stores/useDatasetStore';
import {useProjectStore} from '../../stores/useProjectStore';

function statisticsScope(projectState:ReturnType<typeof useProjectStore.getState>,dataset:ReturnType<typeof useDatasetStore.getState>):string {
  const {project}=projectState;
  return JSON.stringify([project?.id,project?.project_dir,projectState.projectDir,project?.source_dataset_dir,
    project?.task,projectState.task,project?.active_labelset_id||'default',dataset.folderPath,
    dataset.datasetKey,dataset.lastImportedKey,dataset.staleDatasetKeys,dataset.isLoading]);
}

export const DatasetStatisticsPanel:React.FC=()=>{
  const projectState=useProjectStore(),dataset=useDatasetStore();
  const {project,task}=projectState;
  const {folderPath,isLoading,staleDatasetKeys,activeSplitFilter,activeClassFilter,activeLabelFilter}=dataset;
  const scope=statisticsScope(projectState,dataset);
  const [loaded,setLoaded]=useState<{scope:string;value:DatasetStatistics}|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false),[refresh,setRefresh]=useState(0);
  const data=loaded?.scope===scope?loaded.value:null;
  useEffect(()=>{const reload=()=>setRefresh(v=>v+1);window.addEventListener('dataset-statistics-changed',reload);return()=>window.removeEventListener('dataset-statistics-changed',reload);},[]);
  useEffect(()=>{setLoaded(null);setError('');setLoading(false);
    if(!project?.source_dataset_dir||project.source_dataset_dir!==folderPath||project.task!==task||isLoading)return;
    let current=true;
    const isCurrent=()=>current&&scope===statisticsScope(useProjectStore.getState(),useDatasetStore.getState());
    setLoading(true);
    projectPreferences.statistics().then(value=>{
      if(!isCurrent()||value.labelset_id!==(project.active_labelset_id||'default'))return;
      // Reopened import counts may predate image-grain statistics. Both panels
      // use the same active-labelset response, independent of gallery filters.
      useDatasetStore.setState({classes:Object.fromEntries(Object.entries(value.classes).map(([name,entry])=>[name,entry.count])),
        classCountUnit:'images',sourceImages:value.total,unlabeledImages:value.labeling.unlabeled.count});
      setLoaded({scope,value});
    }).catch(e=>{if(isCurrent())setError(e instanceof Error?e.message:String(e));}).finally(()=>{if(isCurrent())setLoading(false);});
    return()=>{current=false;};
  },[scope,staleDatasetKeys,refresh]);
  const filter=(partition:DatasetPartition,label:'all'|'labeled'|'unlabeled'='all',className:string|null=null)=>{
    useDatasetStore.setState({activeSplitFilter:partition,activeLabelFilter:label,activeClassFilter:className,page:1});
    void useDatasetStore.getState().loadImages(1);
  };
  if(!project?.source_dataset_dir)return null;
  const chip=(name:string,count:number,ratio:number,selected:boolean,onClick:()=>void)=><button key={name} type="button" onClick={onClick} className={`rounded-lg border px-3 py-1.5 text-left text-xs ${selected?'border-cyan-500 bg-cyan-950 text-cyan-100':'border-slate-700 bg-slate-900 text-slate-300 hover:border-slate-500'}`}><span>{name}</span><strong className="ml-2 text-white">{count.toLocaleString()}</strong><span className="ml-1 text-[10px] text-slate-400">{(ratio*100).toFixed(1)}%</span></button>;
  const labels:Record<string,string>={train:'학습',val:'검증',test:'시험',not_used:'미사용',not_split:'미분할'};
  return <section aria-label="활성 라벨 세트 데이터 통계" className="shrink-0 space-y-2 border-b border-slate-700 bg-[#111D2B] px-4 py-3">
    <div className="flex items-center gap-3"><h3 className="text-xs font-semibold text-slate-200">데이터 현황</h3><span className="text-[10px] text-slate-400">전체 이미지 {data?.total.toLocaleString()??'—'}개 · 활성 라벨 세트 기준</span><button onClick={()=>setRefresh(v=>v+1)} disabled={loading} aria-label="데이터 통계 새로고침" className="ml-auto rounded border border-slate-600 p-1 text-slate-300"><RefreshCw className={`h-3.5 w-3.5 ${loading?'animate-spin':''}`}/></button></div>
    {data&&<><div className="flex flex-wrap gap-2">{chip('전체',data.total,1,activeSplitFilter==='all'&&activeLabelFilter==='all'&&!activeClassFilter,()=>filter('all'))}{(['labeled','unlabeled'] as const).map(state=>chip(state==='labeled'?'라벨 완료':'라벨 미완료',data.labeling[state].count,data.labeling[state].ratio,activeLabelFilter===state,()=>filter('all',state)))}{Object.entries(data.assignments).map(([partition,values])=>chip(labels[partition],values.count,values.ratio,activeSplitFilter===partition,()=>filter(partition as DatasetPartition)))}</div>
      <details><summary className="cursor-pointer text-[11px] text-slate-400">클래스별 이미지 · 여러 클래스가 있는 이미지는 각각 집계</summary><div className="mt-2 flex max-h-28 flex-wrap gap-2 overflow-auto">{Object.entries(data.classes).map(([name,value])=>chip(name,value.count,value.ratio,activeClassFilter===name,()=>filter('all','all',name)))}</div></details></>}
    {error&&<p role="alert" className="text-xs text-red-300">{error}</p>}
  </section>;
};
