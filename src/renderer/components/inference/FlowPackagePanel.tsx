import React, { useEffect, useState } from 'react';
import { CheckCircle2, PackageCheck, RefreshCw } from 'lucide-react';
import { api, type SavedFlowVersion } from '../../services/api';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useFlowchartStore } from '../../stores/useFlowchartStore';
import { useProjectStore } from '../../stores/useProjectStore';
import { useComputeStore } from '../../stores/useComputeStore';
import type { ImageMeta, VisionTask } from '../../types';
import { savedFlowIdentity, type SavedFlowIdentity } from '../flowchart/flowHandoff';
import { SavedFlowIdentityCard } from '../flowchart/SavedFlowIdentityCard';
import { canVerifyFlowOnHost, edgeDeploymentCommands, flowDeploymentOptions, type EdgeTarget, type FlowDeploymentProfile } from './edgeDeployment';
import {runtimeDeploymentApi} from '../../services/runtimeDeploymentApi';
import {RuntimeOptimizationPanel} from './RuntimeOptimizationPanel';
import {PackageLibraryPanel} from './PackageLibraryPanel';
import {RuntimeServicePanel} from '../runtime/RuntimeServicePanel';
import {useDeliveryScope} from '../runtime/useDeliveryScope';
import {useTaskHandoff} from '../training/useTaskHandoff';
import {productDeliveryApi} from '../../services/productDeliveryApi';
import {reopenOptimizationTask} from './deliveryTaskSelection';
import { flowPackageExport, type FlowApprovalPrerequisites, type FlowExportResult } from '../../services/flowPackageExport';
import { MAX_PARITY_IMAGES, parityFields, parityHeadline, parityTargetLabel, releaseApprovalIds, toggleCohort, type ParityMode } from './flowPackageRelease';
import { ImageLibraryBrowser } from '../common/ImageLibraryBrowser';
import {useParityCohort} from './useParityCohort';
import {parityUnresolvedReason} from './parityCohort';

export const FlowPackagePanel: React.FC<{ sourceFolder: string; task: VisionTask }> = ({ sourceFolder, task }) => {
  const compute = useComputeStore();
  const parityProfile = compute.profiles.find(profile => profile.id === compute.selectedProfileId);
  const {key:deliveryKey,scope:deliveryScope,project}=useDeliveryScope(sourceFolder+task);
  const handoff=useTaskHandoff();const requestedOptimization=handoff?.kind==='optimization'?handoff.jobId:undefined;
  const [libraryRefresh,setLibraryRefresh]=useState(0),[optimizationPath,setOptimizationPath]=useState(''),[deploymentPath,setDeploymentPath]=useState(''),[optimizationJobId,setOptimizationJobId]=useState<string|undefined>();
  useEffect(()=>{setOptimizationPath('');setDeploymentPath('');setOptimizationJobId(undefined);},[deliveryKey]);
  const projectDir = useProjectStore((state) => state.projectDir);
  const setStep = useProjectStore((state) => state.setStep);
  const hasUnsavedDraft = useFlowchartStore((state) => state.pipelineDirty);
  const [versions, setVersions] = useState<SavedFlowVersion[]>([]);
  const [selectedVersionId, setSelectedVersionId] = useState('');
  const [images, setImages] = useState<ImageMeta[]>([]);
  const [selectedImagePath, setSelectedImagePath] = useState('');
  const [parityMode, setParityMode] = useState<ParityMode>('cohort');
  const [cohortRefusal, setCohortRefusal] = useState('');
  const [release, setRelease] = useState<FlowApprovalPrerequisites | null>(null);
  const [releaseError, setReleaseError] = useState<string | null>(null);
  const [releaseSelection, setReleaseSelection] = useState<Record<string, string>>({});
  const [includeApprovals, setIncludeApprovals] = useState(true);
  const [isLoading, setIsLoading] = useState(Boolean(sourceFolder));
  const [isExporting, setIsExporting] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [error, setError] = useState<string | null>(null);
  useEffect(()=>{if(!requestedOptimization)return;let active=true;const started=deliveryScope.current;setOptimizationPath('');setDeploymentPath('');setOptimizationJobId(undefined);setError(null);reopenOptimizationTask(requestedOptimization,project?.source_dataset_dir||sourceFolder,{job:runtimeDeploymentApi.job,packages:productDeliveryApi.packages,select:productDeliveryApi.select},()=>active&&deliveryScope.current===started).then(value=>{if(value){setOptimizationPath(value.package.package_path);setOptimizationJobId(value.job.job_id);setLibraryRefresh(value=>value+1);}}).catch(cause=>{if(active&&deliveryScope.current===started)setError(cause instanceof Error?cause.message:String(cause));});return()=>{active=false;};},[deliveryKey,requestedOptimization,handoff?.taskKey,handoff?.selectionId]);
  const [failedExport, setFailedExport] = useState<{ packagePath?: string; mismatchedFields: string[]; status?: string } | null>(null);
  const [result, setResult] = useState<FlowExportResult | null>(null);
  useEffect(()=>{if(result?.package_path)setLibraryRefresh(value=>value+1);},[result?.package_path]);
  const cohort=useParityCohort({projectId:project?.id,projectDir,source:sourceFolder,task,labelset:project?.active_labelset_id,deliveryKey,legacyImages:images,legacyReady:!isLoading,refreshKey});
  const cohortPicks=cohort.picks,cohortPaths=cohort.paths,cohortLibrary=cohort.mode;
  const unresolvedPaths=cohortPaths.filter(path=>!images.some(image=>image.file_path===path));
  const cohortBlocker=!cohort.ready?'저장된 검증 이미지 선택을 확인하고 있습니다.':cohort.unresolved.length||unresolvedPaths.length?'확인이 필요한 검증 이미지를 다시 선택하거나 목록에서 빼세요.':null;
  const [identity, setIdentity] = useState<SavedFlowIdentity | null>(null);
  const [identityError, setIdentityError] = useState<string | null>(null);
  const [deploymentProfile, setDeploymentProfile] = useState<FlowDeploymentProfile>('standard');
  const [edgeTarget, setEdgeTarget] = useState<EdgeTarget>({ os: 'linux', architecture: 'x86_64' });
  const [hostTarget, setHostTarget] = useState<EdgeTarget | null>(null);
  const [edgeTargets, setEdgeTargets] = useState<Record<EdgeTarget['os'], EdgeTarget['architecture'][]>>({
    linux: ['x86_64', 'arm64'], windows: ['x86_64'], macos: ['arm64'],
  });
  const [edgeTargetError, setEdgeTargetError] = useState<string | null>(null);
  const [deadlineMs,setDeadlineMs]=useState(30000),[cpuThreads,setCPUThreads]=useState(1),[runtimeDevice,setRuntimeDevice]=useState('cpu');
  const [runtimeDevices,setRuntimeDevices]=useState<string[]>(['cpu']);
  const canVerify = Boolean(compute.selectedProfileId) || canVerifyFlowOnHost(deploymentProfile, edgeTarget, hostTarget);
  const deviceChoices = parityProfile ? ['cpu', ...(parityProfile.gpu_selector ? Array.from({length:parityProfile.gpu_selector==='all'?1:parityProfile.gpu_selector.split(',').length},(_,index)=>`cuda:${index}`) : [])] : runtimeDevices;
  useEffect(() => { if (!compute.isLoaded) void compute.load().catch(() => {}); }, [compute.isLoaded]);
  useEffect(() => {
    setRuntimeDevice(parityProfile?.gpu_selector ? 'cuda:0' : 'cpu');
    if (compute.selectedProfileId) setParityMode('cohort');
  }, [compute.selectedProfileId]);
  useEffect(() => {
    let active = true;
    runtimeDeploymentApi.capabilities().then(value=>{if(active)setRuntimeDevices(value.torch_devices);}).catch(()=>{});
    api.export.edgeTargets().then((available) => {
      if (!active) return;
      setHostTarget(available.host);
      setEdgeTargets(available.supported);
      if (available.host) setEdgeTarget(available.host);
    }).catch((cause) => {
      if (active) setEdgeTargetError(cause instanceof Error ? cause.message : 'Edge 대상 정보를 읽지 못했습니다.');
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    setVersions([]);
    setSelectedVersionId('');
    setImages([]);
    setSelectedImagePath('');
    setResult(null);
    setError(null);
    setFailedExport(null);
    if (!sourceFolder) return;
    let active = true;
    setIsLoading(true);
    Promise.all([
      api.flowchart.listPipelines(sourceFolder),
      api.dataset.getImages({ folder_path: sourceFolder, task, limit: 32, split: 'test' }),
    ]).then(async ([saved, testImages]) => {
      const availableImages = testImages.items.length > 0 ? testImages.items
        : (await api.dataset.getImages({ folder_path: sourceFolder, task, limit: 32 })).items;
      if (!active) return;
      setVersions(saved.pipelines);
      setSelectedVersionId(saved.pipelines.find((item) => item.is_active)?.version_id
        || saved.pipelines.find((item) => item.is_latest)?.version_id || saved.pipelines[0]?.version_id || '');
      setImages(availableImages);
      setSelectedImagePath(availableImages[0]?.file_path || '');

    }).catch((cause) => {
      if (active) setError(cause instanceof Error ? cause.message : String(cause));
    }).finally(() => {
      if (active) setIsLoading(false);
    });
    return () => { active = false; };
  }, [sourceFolder, task, projectDir, refreshKey]);

  const selectedVersion = versions.find((item) => item.version_id === selectedVersionId);
  const selectedImage = images.find((item) => item.file_path === selectedImagePath);
  useEffect(() => {
    setIdentity(null);
    setIdentityError(null);
    if (!selectedVersion) return;
    let active = true;
    api.flowchart.getPipelineVersion(selectedVersion.version_id)
      .then((pipeline) => savedFlowIdentity(selectedVersion, pipeline))
      .then((value) => { if (active) setIdentity(value); })
      .catch((cause) => {
        if (active) setIdentityError(cause instanceof Error ? cause.message : '저장된 플로우 정보를 읽지 못했습니다.');
      });
    return () => { active = false; };
  }, [selectedVersionId, versions, sourceFolder, projectDir]);
  useEffect(() => {
    setRelease(null); setReleaseError(null); setReleaseSelection({});
    if (!sourceFolder || !selectedVersion) return;
    let active = true;
    flowPackageExport.prerequisites({ source_dataset_path: sourceFolder, recipe_task: selectedVersion.recipe_task, version_id: selectedVersion.version_id })
      .then((value) => { if (active) { setRelease(value); setReleaseSelection(value.approval_revision_ids); } })
      .catch((cause) => { if (active) setReleaseError(cause instanceof Error ? cause.message : '승인 정보를 읽지 못했습니다.'); });
    return () => { active = false; };
  }, [selectedVersionId, versions, sourceFolder, projectDir, refreshKey]);
  // Parity runs on the device the package will use; the backend rejects any other device.
  const packageDevice = deploymentProfile === 'edge_cpu' ? 'cpu' : deploymentProfile === 'edge_cuda' ? 'cuda:0' : runtimeDevice;
  const effectiveParityMode: ParityMode = canVerify ? parityMode : 'none';
  // image_id stays the file stem the listing always sent, so a library pick runs the flow exactly as a listed one.
  const cohortImages = cohortLibrary === 'available'
    ? cohortPicks.map((item) => ({ file_path: item.file_path, image_id: item.file_name.replace(/\.[^.]+$/, '') }))
    : images.filter((item) => cohortPaths.includes(item.file_path));
  const approvalIds = includeApprovals ? releaseApprovalIds(release, releaseSelection) : null;
  const exportFlow = async () => {
    if (!sourceFolder || !selectedVersion || identity?.versionId !== selectedVersion.version_id || isExporting) return;
    if (targetBlocker) { setError(targetBlocker); return; }
    if(effectiveParityMode==='cohort'&&cohortBlocker){setError(cohortBlocker);return;}
    const parity = parityFields(effectiveParityMode, cohortImages, selectedImage, packageDevice, compute.selectedProfileId);
    if ('error' in parity) {
      setError(parity.error);
      return;
    }
    if (includeApprovals && !approvalIds) {
      setError('승인 포함 패키지는 모든 모델에 검증된 승인 revision을 하나씩 선택해야 합니다. 승인 없이 만들려면 승인 포함을 해제하세요.');
      return;
    }
    setError(null);
    setResult(null);
    setFailedExport(null);
    setIsExporting(true);
    try {
      const exported = await flowPackageExport.exportFlow({
        source_dataset_path: sourceFolder,
        recipe_task: selectedVersion.recipe_task,
        version_id: selectedVersion.version_id,
        package_name: `modu_flow_${selectedVersion.version_id.slice(0, 8)}_${Date.now()}`,
        ...flowDeploymentOptions(deploymentProfile, edgeTarget),
        runtime_config:{deadline_ms:deadlineMs,cpu_threads:cpuThreads,device:packageDevice},
        ...parity.fields,
        ...(approvalIds ? { approval_revision_ids: approvalIds } : {}),
      });
      if (useProjectStore.getState().projectDir === projectDir && useDatasetStore.getState().folderPath === sourceFolder) {
        setResult(exported);
      }
    } catch (cause) {
      if (useProjectStore.getState().projectDir === projectDir) {
        const detail = cause && typeof cause === 'object' ? cause as Record<string, unknown> : null;
        const parity = detail?.parity && typeof detail.parity === 'object'
          ? detail.parity as Record<string, unknown> : null;
        const packagePath = typeof detail?.package_path === 'string' ? detail.package_path : undefined;
        const mismatchedFields = Array.isArray(parity?.mismatched_fields)
          ? parity.mismatched_fields.filter((field): field is string => typeof field === 'string') : [];
        setFailedExport(packagePath || mismatchedFields.length ? { packagePath, mismatchedFields, status: typeof parity?.status === 'string' ? parity.status : undefined } : null);
        setError(typeof detail?.message === 'string' ? detail.message
          : cause instanceof Error ? cause.message : '전체 플로우 패키지를 만들지 못했습니다.');
      }
    } finally {
      setIsExporting(false);
    }
  };

  const targetBlocker = !compute.isLoaded ? compute.loadError || '검증 실행 대상 설정을 확인하고 있습니다.' : compute.selectedProfileId && !parityProfile ? '선택한 검증 서버 설정을 찾을 수 없습니다.' : parityProfile && (parityProfile.allow_sharing || (parityProfile.distributed_processes || 1)>1) ? '패키지 비교에는 GPU 공유·분산 실행을 사용하지 않는 프로필을 선택하세요.' : parityProfile?.memory_budget_mb && packageDevice==='cpu' ? 'CPU 비교에는 CUDA 메모리 예약이 없는 프로필을 선택하세요.' : null;
  const exportBlocker = targetBlocker || (!sourceFolder?'현재 데이터 원본이 필요합니다.':!selectedVersion || identity?.versionId!==selectedVersion.version_id?'저장된 플로우 버전을 선택하세요.':includeApprovals&&!approvalIds?'모든 모델의 검증된 승인 revision을 선택하세요.':effectiveParityMode==='single'&&!selectedImage?'동일성 확인 이미지를 선택하세요.':effectiveParityMode==='cohort'&&cohortBlocker?cohortBlocker:effectiveParityMode==='cohort'&&cohortImages.length<2?'고정 검증 이미지를 2장 이상 선택하세요.':null);
  return <section id="workflow-package" tabIndex={-1} className="rounded-lg border border-[#344255] bg-[#151E2B] p-5" aria-label="전체 검사 플로우 패키지">
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[#344255] pb-4">
      <div className="flex items-start gap-3">
        <div className="rounded-lg border border-sky-700/60 bg-sky-950/50 p-2 text-sky-300"><PackageCheck className="h-5 w-5" /></div>
        <div>
          <h3 className="text-sm font-bold text-slate-100">전체 검사 플로우 내보내기</h3>
          <p className="mt-1 text-xs leading-5 text-slate-400">5단계에 저장한 모델 연결·ROI 전달·조건 분기·최종 판정을 함께 패키징합니다.</p>
        </div>
      </div>
      <button type="button" onClick={() => setRefreshKey((value) => value + 1)} disabled={!sourceFolder || isLoading || isExporting}
        className="flex items-center gap-1.5 rounded border border-[#455670] px-2.5 py-1.5 text-xs text-slate-300 hover:bg-[#243348] disabled:opacity-50">
        <RefreshCw className="h-3.5 w-3.5" /> 저장본 새로고침
      </button>
    </div>
    {hasUnsavedDraft && <div role="status" className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded border border-amber-700/70 bg-amber-950/30 px-3 py-2 text-xs text-amber-200">
      <span>5단계의 미저장 초안은 이 패키지에 포함되지 않습니다. 초안을 사용하려면 5단계에서 저장하세요.</span>
      <button type="button" onClick={() => setStep(5)} className="rounded border border-amber-600 px-2 py-1 font-semibold hover:bg-amber-900/40">5단계에서 저장</button>
    </div>}
    {identity && selectedVersion && identity.versionId === selectedVersion.version_id && <div className="mt-3">
      <SavedFlowIdentityCard identity={identity} isActive={selectedVersion.is_active} />
    </div>}
    {identityError && <p role="alert" className="mt-3 text-xs text-rose-300">저장 버전 확인 실패: {identityError}</p>}
    <label className="mt-3 grid gap-1.5 text-xs text-slate-300">동일성 검증 실행 대상
      <select aria-label="패키지 검증 실행 대상" value={compute.selectedProfileId || ''} disabled={isExporting || !compute.isLoaded}
        onChange={event => void compute.selectTarget(event.target.value || null).catch(cause => setError(cause instanceof Error ? cause.message : String(cause)))}
        className="rounded border border-[#455670] bg-[#0F1723] px-3 py-2">
        <option value="">이 컴퓨터</option>{compute.profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name}{profile.gpu_selector ? ` · GPU ${profile.gpu_selector}` : ' · CPU'}</option>)}
      </select>
      <span className="text-slate-400">{parityProfile ? `${parityProfile.name}에서 앱 참조와 독립 패키지를 함께 실행합니다. 실패하면 이 컴퓨터로 대체하지 않습니다.` : '이 컴퓨터에서 앱 참조와 독립 패키지를 함께 실행합니다.'}</span>
    </label>
    <div className="mt-4 grid gap-3 sm:grid-cols-3">
      <label className="grid gap-1.5 text-xs font-medium text-slate-300">배포 프로필
        <select value={deploymentProfile} disabled={isExporting} onChange={(event) => {
          setDeploymentProfile(event.target.value as FlowDeploymentProfile); setResult(null); setFailedExport(null); setError(null);
          if(event.target.value==='edge_cuda')setEdgeTarget({os:'linux',architecture:edgeTarget.architecture});
        }} className="rounded border border-[#455670] bg-[#0F1723] px-3 py-2 text-xs text-slate-100">
          <option value="standard">표준 Python 패키지 (CPU / CUDA / MPS)</option>
          <option value="edge_cpu">CPU Edge (설치·사전 점검·실행)</option>
          <option value="edge_cuda">CUDA Edge / Jetson (벤더 Runtime 필요)</option>
        </select>
      </label>
      {deploymentProfile !== 'standard' && <>
        <label className="grid gap-1.5 text-xs font-medium text-slate-300">대상 운영체제
          <select value={edgeTarget.os} disabled={isExporting} onChange={(event) => {
            const os = event.target.value as EdgeTarget['os'];
            setEdgeTarget({ os, architecture: edgeTargets[os].includes(edgeTarget.architecture) ? edgeTarget.architecture : edgeTargets[os][0] });
            setResult(null); setFailedExport(null); setError(null);
          }} className="rounded border border-[#455670] bg-[#0F1723] px-3 py-2 text-xs text-slate-100">
            <option value="linux">Linux</option>{deploymentProfile!=='edge_cuda'&&<><option value="windows">Windows</option><option value="macos">macOS</option></>}
          </select>
        </label>
        <label className="grid gap-1.5 text-xs font-medium text-slate-300">대상 CPU 아키텍처
          <select value={edgeTarget.architecture} disabled={isExporting} onChange={(event) => {
            setEdgeTarget({ ...edgeTarget, architecture: event.target.value as EdgeTarget['architecture'] });
            setResult(null); setFailedExport(null); setError(null);
          }} className="rounded border border-[#455670] bg-[#0F1723] px-3 py-2 text-xs text-slate-100">
            {edgeTargets[edgeTarget.os].map((arch) => <option key={arch} value={arch}>{arch === 'arm64' ? 'ARM64 / aarch64' : 'x86_64 / AMD64'}</option>)}
          </select>
        </label>
      </>}
    </div>
    <div className="mt-3 flex flex-wrap gap-3 text-xs text-slate-300"><label>추론 최대 시간 (ms) <input type="number" min={1} max={86400000} value={deadlineMs} disabled={isExporting} onChange={e=>setDeadlineMs(Math.max(1,Math.min(86400000,Math.round(Number(e.target.value)))))} className="w-24 rounded border border-slate-700 bg-slate-900 px-2 py-1"/></label><label>CPU 스레드 <input type="number" min={1} max={64} value={cpuThreads} disabled={isExporting} onChange={e=>setCPUThreads(Math.max(1,Math.min(64,Math.round(Number(e.target.value)))))} className="w-16 rounded border border-slate-700 bg-slate-900 px-2 py-1"/></label>{deploymentProfile==='standard'&&<label>실행 장치 <select value={runtimeDevice} disabled={isExporting} onChange={e=>setRuntimeDevice(e.target.value)} className="bg-slate-900">{deviceChoices.map(device=><option key={device}>{device}</option>)}</select></label>}<span className="text-slate-500">시간 초과 시 실행 프로세스를 종료하고 REVIEW로 기록합니다.</span></div>
    {deploymentProfile === 'edge_cpu' && <p className="mt-2 text-xs leading-5 text-slate-400">
      전체 플로우를 CPU로 실행합니다. 대상에 Python 3.10–3.13과 호환 패키지가 필요하며 설치·사전 점검 CLI를 포함합니다. 특정 보드·벤더 SDK·양자화·현장 성능은 검증하지 않았습니다.
    </p>}
    {deploymentProfile === 'edge_cpu' && edgeTargetError && <p className="mt-2 text-xs text-amber-300">{edgeTargetError} 대상 장비에서 사전 점검을 실행하세요.</p>}
    <div className="grid gap-4 pt-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] lg:items-end">
      <label className="grid min-w-0 gap-1.5 text-xs font-medium text-slate-300">
        저장된 플로우 버전
        <select value={selectedVersionId} onChange={(event) => { setSelectedVersionId(event.target.value); setResult(null); }}
          disabled={!sourceFolder || isLoading || isExporting || versions.length === 0}
          className="w-full rounded border border-[#455670] bg-[#0F1723] px-3 py-2 text-xs text-slate-100 disabled:opacity-50">
          {versions.length === 0 && <option value="">5단계에서 플로우를 먼저 저장하세요</option>}
          {versions.map((version) => <option key={version.version_id} value={version.version_id}>
            {version.is_active ? '[현재 검사 플로우] ' : ''}{version.name} · {version.model_count}모델 · {new Date(version.saved_at).toLocaleString('ko-KR')}
          </option>)}
        </select>
      </label>
      <label className="grid min-w-0 gap-1.5 text-xs font-medium text-slate-300">
        한 장 확인 이미지
        <select value={selectedImagePath} onChange={(event) => { setSelectedImagePath(event.target.value); setResult(null); }}
          disabled={effectiveParityMode !== 'single' || images.length === 0 || isExporting}
          className="w-full rounded border border-[#455670] bg-[#0F1723] px-3 py-2 text-xs text-slate-100 disabled:opacity-50">
          {images.length === 0 && <option value="">사용 가능한 이미지 없음</option>}
          {images.map((item) => <option key={item.file_path} value={item.file_path}>{item.file_name}</option>)}
        </select>
      </label>
      {exportBlocker&&<div className="text-xs text-amber-200"><p id="flow-export-reason">내보내기 보류: {exportBlocker}</p><button className="workspace-button mt-2" onClick={()=>void setStep(!sourceFolder?1:!selectedVersion?5:includeApprovals&&!approvalIds?4:6)}>{!sourceFolder?'데이터 원본 확인 (1단계)':!selectedVersion?'플로우 저장·평가 (5단계)':includeApprovals&&!approvalIds?'평가·승인 확인 (4단계)':'검증 이미지 선택 (6단계)'}</button></div>}
      <button type="button" aria-describedby={exportBlocker?'flow-export-reason':undefined} onClick={exportFlow} disabled={Boolean(targetBlocker) || !selectedVersion || identity?.versionId !== selectedVersion.version_id || !sourceFolder || isExporting
          || (effectiveParityMode === 'single' && !selectedImage) || (effectiveParityMode === 'cohort' && (Boolean(cohortBlocker) || cohortImages.length < 2)) || (includeApprovals && !approvalIds)}
        className="rounded bg-sky-600 px-4 py-2 font-bold text-white hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50">
        {isExporting ? '패키지 생성·검증 중...' : '전체 플로우 내보내기'}
      </button>
    </div>
    <fieldset className="mt-4 rounded border border-[#344255] p-3 text-xs text-slate-300" disabled={isExporting}>
      <legend className="px-1 font-semibold text-slate-200">승인 revision</legend>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={includeApprovals} onChange={(event) => setIncludeApprovals(event.target.checked)}
          className="rounded border-[#455670] bg-[#0F1723] text-sky-500" />
        현장 서비스에 적용할 승인 포함 패키지로 만들기
      </label>
      {releaseError && <p role="alert" className="mt-2 text-rose-300">승인 정보 확인 실패: {releaseError}</p>}
      {includeApprovals && release && <ul className="mt-2 grid gap-2">
        {release.models.map((model) => <li key={model.job_id} className="grid gap-1 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] sm:items-center">
          <span className="min-w-0 break-all font-mono text-slate-400">{model.task} · {model.job_id} · {model.checkpoint_sha256.slice(0, 12)}</span>
          {model.candidates.length ? <select value={releaseSelection[model.job_id] || ''} aria-label={`${model.job_id} 승인 revision`}
            onChange={(event) => setReleaseSelection((current) => ({ ...current, [model.job_id]: event.target.value }))}
            className="rounded border border-[#455670] bg-[#0F1723] px-2 py-1.5 text-slate-100">
            <option value="">검증된 승인 revision 선택</option>
            {model.candidates.map((candidate) => <option key={candidate.revision_id} value={candidate.revision_id}>
              {candidate.is_active ? '[현재 활성] ' : ''}{candidate.action} · {candidate.reviewer} · {new Date(candidate.created_at).toLocaleString('ko-KR')}
            </option>)}
          </select> : <span className="text-amber-300">이 체크포인트에 검증된 승인이 없습니다. 4단계에서 평가·승인한 뒤 다시 확인하세요.</span>}
        </li>)}
      </ul>}
      {includeApprovals && release && !approvalIds && <p className="mt-2 text-amber-300">모든 모델에 승인 revision을 하나씩 선택해야 승인 포함 패키지를 만들 수 있습니다. 승인은 여기서 새로 만들지 않습니다.</p>}
      {!includeApprovals && <p className="mt-2 text-slate-500">승인 없는 패키지는 내보내기·시험용이며 현장 서비스 적용 단계에서 거부됩니다.</p>}
    </fieldset>
    <fieldset className="mt-3 rounded border border-[#344255] p-3 text-xs text-slate-300" disabled={isExporting || !canVerify}>
      <legend className="px-1 font-semibold text-slate-200">앱 엔진 · 독립 실행 패키지 동일성 검증</legend>
      <div className="flex flex-wrap gap-4">
        {([['cohort', '고정 이미지 여러 장'], ['single', '한 장 (제한된 확인)'], ['none', '검증 안 함']] as const).map(([mode, label]) =>
          <label key={mode} className="flex items-center gap-1.5"><input type="radio" name="flow-parity-mode" checked={effectiveParityMode === mode}
            disabled={mode==='single'&&Boolean(compute.selectedProfileId)} onChange={() => { setParityMode(mode); setResult(null); }} className="border-[#455670] bg-[#0F1723] text-sky-500" />{label}</label>)}
      </div>
      <p className="mt-2 text-slate-400">대상 장치 <span className="font-mono text-slate-200">{packageDevice}</span> · 패키지 실행 장치와 같은 장치로만 비교합니다.</p>
      {effectiveParityMode === 'cohort' && cohortLibrary === 'available' && <div className="mt-2 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <span>{cohortImages.length}장 선택 (2–{MAX_PARITY_IMAGES}장) · 검증된 데이터 버전 전체에서 선택</span>
          <button type="button" disabled={cohort.phase==='loading'} onClick={() => { cohort.clear(); setCohortRefusal(''); }} className="rounded border border-[#455670] px-2 py-0.5 hover:bg-[#243348] disabled:opacity-50">선택 해제</button>
        </div>
        {cohortPicks.length>0&&<ul aria-label="선택한 패키지 검증 이미지" className="flex flex-wrap gap-1">{cohortPicks.map(item=><li key={item.image_uuid}><button type="button" onClick={()=>cohort.remove(item.image_uuid)} className="rounded border border-sky-800 px-2 py-1 text-sky-200" title="선택 해제">{item.relative_path} ✕</button></li>)}</ul>}
        {cohort.unresolved.length>0&&<div className="rounded border border-amber-800 bg-amber-950/20 p-2"><p className="text-amber-200">저장된 이미지 {cohort.unresolved.length}장의 확인이 필요합니다. 재선택하거나 빼기 전에는 비교 패키지를 만들 수 없습니다.</p><ul aria-label="확인이 필요한 패키지 검증 이미지" className="mt-2 flex flex-wrap gap-1">{cohort.unresolved.map(item=><li key={item.image_uuid}><button type="button" onClick={()=>cohort.remove(item.image_uuid)} className="rounded border border-amber-700 px-2 py-1 text-amber-200" title="저장 목록에서 빼기">{item.relative_path} · {parityUnresolvedReason(item.status)} ✕</button></li>)}</ul></div>}
        {cohortPicks.length + cohort.unresolved.length >= MAX_PARITY_IMAGES && <p className="text-amber-300">최대 {MAX_PARITY_IMAGES}장까지 고를 수 있습니다.</p>}
        {cohortRefusal && <p role="status" className="text-amber-300">{cohortRefusal}</p>}
        <div className="flex h-[320px] flex-col">
          {cohort.ready&&<ImageLibraryBrowser key={cohort.token} selectedIds={new Set(cohortPicks.map((item) => item.image_uuid))} initialFilters={{ state: 'valid' }}
            onPick={(item) => {
              const picked = cohortPicks.some((pick) => pick.image_uuid === item.image_uuid);
              // a corrupt image cannot be compared; the click is answered instead of ignored
              setCohortRefusal(!picked && !item.valid ? `잘못된 이미지는 동일성 비교에 쓸 수 없습니다: ${item.relative_path}` : '');
              if(cohort.ready)cohort.pick(item);
            }}
            onUnavailable={cohort.unavailable} />}
        </div>
      </div>}
      {effectiveParityMode === 'cohort' && cohortLibrary === 'unavailable' && <div className="mt-2">
        <div className="flex flex-wrap items-center gap-2">
          <span>{cohortImages.length}장 선택 (2–{MAX_PARITY_IMAGES}장) · 검증 버전 없음: 처음 32장 목록을 경로로 기억</span>
          <button type="button" disabled={!cohort.ready} onClick={() => cohort.pathsChange(() => images.slice(0, MAX_PARITY_IMAGES).map((item) => item.file_path))}
            className="rounded border border-[#455670] px-2 py-0.5 hover:bg-[#243348]">목록 전체</button>
          <button type="button" disabled={cohort.phase==='loading'} onClick={() => cohort.clear()} className="rounded border border-[#455670] px-2 py-0.5 hover:bg-[#243348] disabled:opacity-50">선택 해제</button>
        </div>
        {unresolvedPaths.length>0&&<div className="mt-2 text-amber-200"><p>목록 밖 저장 경로는 확인 후 빼거나 데이터 버전을 검증해 다시 선택하세요.</p><ul aria-label="목록 밖 패키지 검증 경로">{unresolvedPaths.map(path=><li key={path}><button type="button" className="underline" onClick={()=>cohort.pathsChange(rows=>rows.filter(row=>row!==path))}>{path} ✕</button></li>)}</ul></div>}
        <ul className="mt-2 grid max-h-40 gap-1 overflow-y-auto sm:grid-cols-2">
          {images.map((item) => <li key={item.file_path}><label className="flex min-w-0 items-center gap-1.5">
            <input type="checkbox" disabled={!cohort.ready} checked={cohortPaths.includes(item.file_path)} onChange={() => cohort.pathsChange((current) => toggleCohort(current, item.file_path))}
              className="rounded border-[#455670] bg-[#0F1723] text-sky-500" />
            <span className="truncate">{item.file_name}</span></label></li>)}
        </ul>
      </div>}
      {effectiveParityMode==='cohort'&&!cohort.ready&&!cohort.error&&<p role="status" className="mt-2 text-slate-400">저장된 검증 이미지 선택을 확인하고 있습니다.</p>}
      {cohort.error&&<p role="alert" className="mt-2 text-amber-300">{cohort.error} <button type="button" onClick={cohort.retry} className="underline">선택 다시 확인</button></p>}
      {effectiveParityMode === 'single' && <p className="mt-2 text-amber-300">한 장 CPU 확인은 호환용 제한 검증이며 여러 장·대상 장치 수락 근거로 쓰이지 않습니다.</p>}
    </fieldset>
    {!canVerify && <p role="status" className="mt-2 text-xs text-amber-300">선택한 Edge 대상과 현재 앱의 OS/CPU가 다르거나 현재 대상 정보가 없습니다. 이미지 결과 비교는 대상 장비에서 실행하세요.</p>}
    {error && <div role="alert" className="mt-3 rounded border border-rose-700 bg-rose-950/30 p-3 text-xs text-rose-200">
      <p>{error}</p>
      {failedExport?.status && <p className="mt-2">동일성 검증 결과: {failedExport.status === 'mismatch' ? '불일치' : failedExport.status === 'failed' ? '실행 실패' : failedExport.status} (패키지에 실패 기록 저장)</p>}
      {failedExport?.mismatchedFields.length ? <p className="mt-2 break-all font-mono">불일치 항목: {failedExport.mismatchedFields.join(', ')}</p> : null}
      {failedExport?.packagePath && <p className="mt-2 break-all font-mono">검증 실패 패키지: {failedExport.packagePath}</p>}
      {failedExport && <button type="button" onClick={() => void exportFlow()} disabled={isExporting}
        className="mt-2 rounded border border-rose-500 px-2 py-1 font-semibold hover:bg-rose-900/50 disabled:opacity-50">같은 입력으로 다시 생성·검증</button>}
    </div>}
    {result && <div className="mt-4 rounded border border-[#455670] bg-[#0E1722] p-3 text-xs">
      <div className="flex flex-wrap items-center gap-2 text-slate-100">
        <CheckCircle2 className="h-4 w-4 text-emerald-400" /> 패키지 생성 완료 · 모델 {result.model_job_ids.length}개 · 파일 {result.total_files}개
        {(() => { const headline = parityHeadline(result.parity); return <span className={`rounded border px-2 py-0.5 ${headline.tone === 'ok' ? 'border-emerald-700 text-emerald-300' : headline.tone === 'fail' ? 'border-rose-700 text-rose-300' : 'border-amber-700 text-amber-300'}`}>{headline.text}</span>; })()}
      </div>
      {result.parity.status === 'passed' && result.parity.scope === 'cohort' && <p className="mt-2 text-slate-300">판정 분포 {Object.entries(result.parity.verdict_counts || {}).map(([verdict, count]) => `${verdict} ${count}`).join(' · ')} · 입력 묶음 {result.parity.cohort_sha256?.slice(0, 12)}</p>}
      {result.parity.status === 'passed' && result.parity.scope === 'single_image' && <p className="mt-2 text-slate-300">최종 판정 {result.parity.final_verdict} · ROI {result.parity.roi_count}개</p>}
      <p className="mt-2 text-slate-300">검증 실행 기록: {parityTargetLabel(result.parity)}</p>
      <p className="mt-2 break-all font-mono text-slate-400">{result.package_path}</p>
      {result.deployment && <div className="mt-3 rounded border border-sky-800 bg-sky-950/20 p-3">
        <p className="font-semibold text-sky-200">{result.deployment.profile} · {result.deployment.target.os} / {result.deployment.target.architecture} · {result.deployment.device}</p>
        <p className="mt-2 text-slate-400">패키지 폴더를 대상 장비로 복사한 뒤 아래 순서로 실행하세요.</p>
        <pre className="mt-2 overflow-x-auto whitespace-pre text-[11px] text-slate-300">{Object.values(edgeDeploymentCommands(result.deployment.target)).join('\n')}</pre>
      </div>}
      <p className="mt-2 text-slate-500">{result.parity.scope === 'cohort' ? '앱 엔진과 독립 실행 패키지가 같은 고정 입력·장치에서 같은 판정을 냈다는 기록입니다.' : result.parity.scope === 'single_image' ? '이미지 1장 CPU 확인이며 현장 수락 근거가 아닙니다.' : '동일성 검증을 실행하지 않았습니다.'} 실제 대상 장비 실행과 현장 서비스 적용은 별도로 확인하세요.</p>
      <div className="mt-3 rounded border border-slate-700 p-3"><h4 className="font-semibold">Python · C++ · C# Predictor / Executor</h4><p className="mt-1 text-slate-400">전체 DAG와 모든 연결 모델의 원본 좌표·판정·측정 결과를 같은 JSON으로 제공합니다.</p><pre className="mt-2 overflow-auto text-[11px]">{'python native_runtime/build_native.py --output native-build\nnative-build/vision_predict /absolute/package /absolute/image.png 30000\ndotnet build native_runtime/VisionRuntime.csproj -o native-build/csharp'}</pre><p className="mt-1 text-slate-500">C# 출력 폴더에 빌드한 네이티브 라이브러리를 복사하세요. CPython 개발 헤더와 대상 아키텍처의 의존성이 필요합니다.</p></div>
      {!optimizationPath&&<RuntimeOptimizationPanel packagePath={result.package_path} sourceFolder={sourceFolder} task={task}/>}
    </div>}
    <PackageLibraryPanel sourceFolder={sourceFolder} task={task} refreshKey={libraryRefresh} onSelected={row=>{if(row.version_id)setSelectedVersionId(row.version_id);if(handoff?.kind==='export'){setDeploymentPath(row.package_path);setOptimizationPath('');setOptimizationJobId(undefined);}}} onOptimize={row=>{setOptimizationPath(row.package_path);setOptimizationJobId(undefined);setDeploymentPath('');}} onDeploy={row=>{setDeploymentPath(row.package_path);setOptimizationPath('');setOptimizationJobId(undefined);}}/>
    {optimizationPath&&<RuntimeOptimizationPanel key={optimizationPath} packagePath={optimizationPath} sourceFolder={sourceFolder} task={task} initialJobId={optimizationJobId}/>}
    {deploymentPath&&<RuntimeServicePanel key={deploymentPath} projectDir={projectDir} initialPackagePath={deploymentPath}/>}
  </section>;
};
