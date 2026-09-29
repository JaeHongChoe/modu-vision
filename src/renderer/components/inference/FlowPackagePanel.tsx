import React, { useEffect, useState } from 'react';
import { CheckCircle2, PackageCheck, RefreshCw } from 'lucide-react';
import { api, type SavedFlowVersion } from '../../services/api';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useFlowchartStore } from '../../stores/useFlowchartStore';
import { useProjectStore } from '../../stores/useProjectStore';
import type { ImageMeta, VisionTask } from '../../types';
import { savedFlowIdentity, type SavedFlowIdentity } from '../flowchart/flowHandoff';
import { SavedFlowIdentityCard } from '../flowchart/SavedFlowIdentityCard';

interface FlowExportResult {
  package_path: string;
  package_name: string;
  pipeline_id: string;
  model_job_ids: string[];
  total_files: number;
  parity: {
    status: 'not_run' | 'passed';
    image_path?: string;
    final_verdict?: string;
    roi_count?: number;
    compared_fields?: string[];
  };
}

export const FlowPackagePanel: React.FC<{ sourceFolder: string; task: VisionTask }> = ({ sourceFolder, task }) => {
  const projectDir = useProjectStore((state) => state.projectDir);
  const setStep = useProjectStore((state) => state.setStep);
  const hasUnsavedDraft = useFlowchartStore((state) => state.pipelineDirty);
  const [versions, setVersions] = useState<SavedFlowVersion[]>([]);
  const [selectedVersionId, setSelectedVersionId] = useState('');
  const [images, setImages] = useState<ImageMeta[]>([]);
  const [selectedImagePath, setSelectedImagePath] = useState('');
  const [verifyOnImage, setVerifyOnImage] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [failedExport, setFailedExport] = useState<{ packagePath?: string; mismatchedFields: string[] } | null>(null);
  const [result, setResult] = useState<FlowExportResult | null>(null);
  const [identity, setIdentity] = useState<SavedFlowIdentity | null>(null);
  const [identityError, setIdentityError] = useState<string | null>(null);

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
  const exportFlow = async () => {
    if (!sourceFolder || !selectedVersion || identity?.versionId !== selectedVersion.version_id || isExporting) return;
    if (verifyOnImage && !selectedImage) {
      setError('동일성 검증에 사용할 실제 이미지를 선택하세요.');
      return;
    }
    setError(null);
    setResult(null);
    setFailedExport(null);
    setIsExporting(true);
    try {
      const exported = await api.export.flow({
        source_dataset_path: sourceFolder,
        recipe_task: selectedVersion.recipe_task,
        version_id: selectedVersion.version_id,
        package_name: `modu_flow_${selectedVersion.version_id.slice(0, 8)}_${Date.now()}`,
        ...(verifyOnImage && selectedImage ? {
          verification_image_path: selectedImage.file_path,
          verification_image_id: selectedImage.image_id,
        } : {}),
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
        setFailedExport(packagePath || mismatchedFields.length ? { packagePath, mismatchedFields } : null);
        setError(typeof detail?.message === 'string' ? detail.message
          : cause instanceof Error ? cause.message : '전체 플로우 패키지를 만들지 못했습니다.');
      }
    } finally {
      setIsExporting(false);
    }
  };

  return <section className="rounded-lg border border-[#344255] bg-[#151E2B] p-5" aria-label="전체 검사 플로우 패키지">
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
        검증 이미지
        <select value={selectedImagePath} onChange={(event) => { setSelectedImagePath(event.target.value); setResult(null); }}
          disabled={!verifyOnImage || images.length === 0 || isExporting}
          className="w-full rounded border border-[#455670] bg-[#0F1723] px-3 py-2 text-xs text-slate-100 disabled:opacity-50">
          {images.length === 0 && <option value="">사용 가능한 이미지 없음</option>}
          {images.map((item) => <option key={item.file_path} value={item.file_path}>{item.file_name}</option>)}
        </select>
      </label>
      <button type="button" onClick={exportFlow} disabled={!selectedVersion || identity?.versionId !== selectedVersion.version_id || !sourceFolder || isExporting || (verifyOnImage && !selectedImage)}
        className="rounded bg-sky-600 px-4 py-2 font-bold text-white hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50">
        {isExporting ? '패키지 생성·검증 중...' : '전체 플로우 내보내기'}
      </button>
    </div>
    <label className="mt-3 flex items-center gap-2 text-xs text-slate-300">
      <input type="checkbox" checked={verifyOnImage} onChange={(event) => setVerifyOnImage(event.target.checked)} disabled={isExporting}
        className="rounded border-[#455670] bg-[#0F1723] text-sky-500" />
      실제 이미지로 앱 엔진과 독립 실행 패키지 결과 비교
    </label>
    {error && <div role="alert" className="mt-3 rounded border border-rose-700 bg-rose-950/30 p-3 text-xs text-rose-200">
      <p>{error}</p>
      {failedExport?.mismatchedFields.length ? <p className="mt-2 break-all font-mono">불일치 항목: {failedExport.mismatchedFields.join(', ')}</p> : null}
      {failedExport?.packagePath && <p className="mt-2 break-all font-mono">검증 실패 패키지: {failedExport.packagePath}</p>}
      {failedExport && <button type="button" onClick={() => void exportFlow()} disabled={isExporting}
        className="mt-2 rounded border border-rose-500 px-2 py-1 font-semibold hover:bg-rose-900/50 disabled:opacity-50">같은 이미지로 다시 생성·검증</button>}
    </div>}
    {result && <div className="mt-4 rounded border border-[#455670] bg-[#0E1722] p-3 text-xs">
      <div className="flex flex-wrap items-center gap-2 text-slate-100">
        <CheckCircle2 className="h-4 w-4 text-emerald-400" /> 패키지 생성 완료 · 모델 {result.model_job_ids.length}개 · 파일 {result.total_files}개
        <span className={`rounded border px-2 py-0.5 ${result.parity.status === 'passed' ? 'border-emerald-700 text-emerald-300' : 'border-amber-700 text-amber-300'}`}>
          {result.parity.status === 'passed' ? '실제 이미지 결과 일치' : '실제 이미지 동일성 미검증'}
        </span>
      </div>
      {result.parity.status === 'passed' && <p className="mt-2 text-slate-300">최종 판정 {result.parity.final_verdict} · ROI {result.parity.roi_count}개</p>}
      <p className="mt-2 break-all font-mono text-slate-400">{result.package_path}</p>
      <p className="mt-2 text-slate-500">이 결과는 선택한 이미지 1장의 동일성 검증입니다. 현장 서비스 적용 여부는 별도로 확인하세요.</p>
    </div>}
  </section>;
};
