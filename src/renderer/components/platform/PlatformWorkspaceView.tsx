import type {RefObject} from 'react';
import {GetiPlatformController, platformJobActive, platformJobLabel, platformLicenseName, platformLicenseUrl, platformNextAction, platformTargetSupported, platformTrainingTask, type PlatformJob, type PlatformWorkspaceState} from '../../services/getiPlatform';

const taskNames: Record<string,string> = {classification:'사진 분류',patch_classification:'패치 기반 사진 분류',detection:'물체 위치 찾기',instance_segmentation:'개체별 영역 분할',segmentation:'시맨틱 영역 분할',anomaly:'이상 찾기',ocr:'문자 읽기',rotated_detection:'회전 물체 찾기',rotation:'각도 보정',enhancement:'이미지 개선',gan:'결함 합성',defect_gan:'결함 합성',super_resolution:'해상도 개선'};
const count = (value:number) => Number.isFinite(value) ? value.toLocaleString('ko-KR') : '확인 필요';
const nativeName = (task:string) => taskNames[task] || task;
const buttons = 'workspace-button';

export interface PlatformWorkspaceViewProps {
  state: PlatformWorkspaceState; controller: GetiPlatformController; hasSource: boolean;
  projectName: string; task: string; preparationCount?: number; preparationSummary?: string;
  onNavigate?: (step:number) => void; onClose?: () => void; epochRef?: RefObject<HTMLInputElement>;
  onUseAdoptedLabels?: () => void;
  onShowResults?: () => void; resultsRef?: RefObject<HTMLElement>;
}
function JobRecord({job,controller,disabled,canCancel}:{job:PlatformJob;controller:GetiPlatformController;disabled:boolean;canCancel:boolean}) {
  const active = platformJobActive(job), progress = typeof job.progress === 'number' && Number.isFinite(job.progress) && job.progress >= 0 && job.progress <= 100 ? job.progress : null;
  const cancelPending = !!job.cancel_requested || ['cancel_requested','cancelling','stopping'].includes(job.status);
  return <li className="workspace-record platform-job">
    <div className="platform-heading-row"><strong>{platformJobLabel(job)}</strong><span>{job.architecture_id || '학습 작업'}</span></div>
    {active && <div className="platform-progress"><progress aria-label="학습 진행" max={100} {...(progress === null ? {} : {value:progress})}/><span>{progress === null ? '진행률 확인 중' : `${progress.toLocaleString('ko-KR')}%`}</span></div>}
    {job.message && <p>{job.message}</p>}
    {job.error && <p className="platform-error" role="alert">{job.error}</p>}
    {active && <p className="workspace-description">{cancelPending || ['cancelled','canceled','aborted'].includes(job.status) ? '중단 요청과 실제 작업자 종료를 확인 중입니다.' : job.status === 'submission_unconfirmed' ? '제출 결과를 아직 확인하지 못했습니다. 새 학습을 시작하지 말고 저장 상태를 확인하세요.' : '이 화면을 닫아도 서버의 학습 작업은 계속됩니다.'}</p>}
    <div className="workspace-toolbar">
      <button type="button" className={buttons} disabled={disabled} onClick={() => {if (!disabled) void controller.refreshJob(job.id);}}>저장 상태 확인</button>
      {active && canCancel && job.status !== 'submission_unconfirmed' && <button type="button" className={`${buttons} workspace-button--quiet`} disabled={disabled || cancelPending} onClick={() => {if (!disabled && !cancelPending) void controller.cancel(job.id);}}>{cancelPending ? '중단 요청됨' : '학습 중단 요청'}</button>}
    </div>
    <details className="workspace-disclosure"><summary>작업 식별 정보</summary><dl><dt>작업 ID</dt><dd>{job.id}</dd><dt>플랫폼 작업</dt><dd>{job.platform_job_id || '확인되지 않음'}</dd><dt>작업자 종료</dt><dd>{job.worker_exited === true ? '확인됨' : '확인되지 않음'}</dd></dl></details>
  </li>;
}
function evaluationRows(evaluations: unknown[]): Array<{subset:string;revision:string;name:string;value:string}> {
  const rows: Array<{subset:string;revision:string;name:string;value:string}> = [];
  for (const item of evaluations) {
    if (!item || typeof item !== 'object') continue;
    const evaluation = item as {subset?:unknown;dataset_revision_id?:unknown;metrics?:unknown};
    if (!Array.isArray(evaluation.metrics)) continue;
    for (const metric of evaluation.metrics) {
      if (!metric || typeof metric !== 'object' || typeof metric.name !== 'string' || typeof metric.value !== 'number' || !Number.isFinite(metric.value)) continue;
      rows.push({subset:typeof evaluation.subset === 'string' ? evaluation.subset : '분할 미기록',revision:typeof evaluation.dataset_revision_id === 'string' ? evaluation.dataset_revision_id : '미기록',name:metric.name,value:metric.value.toLocaleString('ko-KR',{maximumFractionDigits:6})});
    }
  }
  return rows;
}
function predictionRows(predictions: unknown[] | undefined): Array<{label:string;confidence:number|null;shape:string}> {
  if (!Array.isArray(predictions)) return [];
  return predictions.slice(0,100).flatMap(item => {
    if (!item || typeof item !== 'object') return [];
    const prediction = item as {label?:unknown;confidence?:unknown;shape?:{type?:unknown}};
    if (typeof prediction.label !== 'string') return [];
    return [{label:prediction.label,confidence:typeof prediction.confidence === 'number' && Number.isFinite(prediction.confidence) ? prediction.confidence : null,
      shape:prediction.shape?.type === 'rectangle' ? '상자 위치' : prediction.shape?.type === 'polygon' ? '개체 영역' : '사진 전체'}];
  });
}

/** Pure presentation. Every write below requires a named click and controller authority checks. */
export function PlatformWorkspaceView({state,controller,hasSource,projectName,task,preparationCount=0,preparationSummary,onNavigate,onClose,epochRef,onShowResults,resultsRef,onUseAdoptedLabels}:PlatformWorkspaceViewProps) {
  const status = state.status, busy = !!state.busy || state.loading;
  const next = platformNextAction(state.error ? null : status,{hasSource,architectureId:state.architectureId,uncertain:state.uncertain,targetTask:state.targetTask,inputRecipeOperations:preparationCount});
  const navigation = ['import','native'].includes(next.kind);
  const disabled = busy || !!next.disabled || (navigation && !onNavigate);
  const architecture = status?.architectures.find(row => row.id === state.architectureId);
  const licenseName = platformLicenseName(architecture), licenseUrl = platformLicenseUrl(architecture);
  const receipt = status?.dataset, selectedModel = status?.models.find(row => row.id === state.modelId), variant = selectedModel?.variants.find(row => row.id === state.variantId);
  const writeBlocked = busy || !!state.error || !!status?.runtime.degraded || state.uncertain;
  const activeJobs = status?.jobs.filter(platformJobActive) ?? [], recentJobs = status?.jobs.filter(row => !platformJobActive(row)) ?? [];
  const unconfirmed = state.uncertain || !!status?.jobs.some(job => ['submission_unconfirmed','unknown','uncertain'].includes(job.status));
  const trainingTask = status ? platformTrainingTask(status,state.targetTask) : task;
  const supportedTarget = !!status && platformTargetSupported(status,state.targetTask);
  const metrics = evaluationRows(variant?.evaluations ?? []), predictions = predictionRows(state.prediction?.predictions);
  const runPrimary = () => {
    if (disabled) return;
    if (next.kind === 'import') onNavigate?.(1);
    else if (next.kind === 'native') onNavigate?.(3);
    else if (next.kind === 'start') void controller.start();
    else if (next.kind === 'prepare') void controller.prepare();
    else if (next.kind === 'weights') void controller.weights();
    else if (next.kind === 'train') void controller.train();
    else if (next.kind === 'results') onShowResults?.();
    else void controller.refresh();
  };
  const navigate = (step:number) => {if (!busy && onNavigate) onNavigate(step);};
  return <section className="platform-workspace" data-testid="geti-platform-workspace" aria-labelledby="platform-workspace-title" aria-busy={busy}>
    <header className="platform-workspace__header">
      <div><p className="platform-eyebrow">현재 프로젝트의 학습 작업대</p><h2 id="platform-workspace-title">{projectName}</h2><p className="workspace-description">{nativeName(task)} · 현재 계정과 프로젝트의 데이터로 이어갑니다.</p></div>
      {onClose && <button type="button" className={`${buttons} workspace-button--quiet`} onClick={onClose}>기존 작업대로 돌아가기</button>}
    </header>
    <nav aria-label="기존 작업 단계 열기" className="platform-goals">
      {[{name:'사진 준비',step:1,note:'사진·정답·분할'},{name:'AI 만들기',step:3,note:'전처리·모델·학습'},{name:'결과 확인',step:4,note:'독립 시험·비교'},{name:'검사 사용',step:6,note:'검사 흐름·실행'}].map(goal => <button type="button" className={`${buttons} workspace-button--quiet`} key={goal.name} disabled={busy || !onNavigate} onClick={() => navigate(goal.step)}><strong>{goal.name}</strong><span>{goal.note}</span></button>)}
    </nav>
    <div className="platform-workspace__grid">
      <div className="platform-workspace__content workspace-stack">
        <section className="workspace-section" aria-labelledby="platform-data-title">
          <div className="workspace-section__header"><div><h3 id="platform-data-title">이번 학습에 사용할 사진</h3><p className="workspace-description">원본과 기존 라벨은 그대로 두고, 저장한 버전을 작업 복사본으로 준비합니다.</p></div><span className="workspace-badge" data-tone={receipt ? 'success':'info'}>{receipt ? '학습 기준 고정됨' : hasSource ? '데이터 준비 전' : '사진 가져오기 전'}</span></div>
          {receipt ? <><dl className="platform-counts"><div><dt>학습</dt><dd>{count(receipt.counts.training)}장</dd></div><div><dt>검증</dt><dd>{count(receipt.counts.validation)}장</dd></div><div><dt>시험</dt><dd>{count(receipt.counts.testing)}장</dd></div></dl><p className="workspace-description">검증은 모델 선택에, 별도 시험은 결과 확인에 사용합니다. 학습 완료만으로 품질이 승인되지는 않습니다.</p></> : <p className="platform-empty">{hasSource ? '사진·라벨·분할을 확인한 뒤 학습용 데이터를 준비하세요. 준비가 거절되면 아래 오류에서 필요한 입력을 확인할 수 있습니다.' : '먼저 사진 준비 화면에서 현재 프로젝트에 사진을 가져오세요.'}</p>}
          <div className="workspace-toolbar"><button type="button" className={buttons} disabled={busy || !onNavigate} onClick={() => navigate(1)}>사진·분할 화면 열기</button><button type="button" className={`${buttons} workspace-button--quiet`} disabled={busy || !onNavigate} onClick={() => navigate(2)}>정답 라벨 화면 열기</button>{receipt && <button type="button" className={`${buttons} workspace-button--quiet`} disabled={writeBlocked || !status?.permissions.can_prepare || status?.runtime.state !== 'running' || activeJobs.length > 0} onClick={() => {if (!writeBlocked) void controller.prepare();}}>현재 데이터로 새 학습 기준 준비</button>}</div>
          <details className="workspace-disclosure"><summary>학습 기준과 데이터 식별 정보</summary>{receipt ? <dl className="platform-identities"><dt>데이터 버전</dt><dd>{receipt.dataset_version_id}</dd><dt>라벨셋</dt><dd>{receipt.labelset_id}</dd><dt>복사본</dt><dd>{receipt.id}</dd><dt>분할 해시</dt><dd>{receipt.split_sha256}</dd><dt>입력 규칙 해시</dt><dd>{receipt.recipe_sha256 || '기록된 규칙 없음'}</dd><dt>검토 기준</dt><dd>{receipt.revision || '기록 없음 · 라벨 채택 불가'}</dd></dl> : <p>아직 고정된 학습 기준이 없습니다.</p>}</details>
        </section>
        <section className="workspace-section" aria-labelledby="platform-prepare-title">
          <div className="workspace-section__header"><div><h3 id="platform-prepare-title">입력 전처리와 모델</h3><p className="workspace-description">{preparationSummary || (preparationCount ? `저장한 입력 전처리 ${count(preparationCount)}개를 복사본과 추론에 함께 적용합니다.` : '저장한 고정 입력 전처리가 없습니다. 원본 입력 기준으로 준비합니다.')}</p></div></div>
          {status?.project.task === 'segmentation' && status.project.target_task_options?.includes('instance_segmentation') && !receipt && <div className="workspace-record"><h4>개체마다 따로 구분해야 하나요?</h4><p>개체별 분할은 각 물체에 저장한 개별 다각형 라벨만 사용합니다. 브러시·시맨틱 마스크를 개체 라벨로 바꾸지 않습니다. 현재 프로젝트의 작업 종류는 시맨틱 분할로 유지됩니다.</p>{state.targetTask === 'instance_segmentation' ? <><p role="status"><strong>이번 플랫폼 학습: 개체별 영역 분할</strong></p>{preparationCount > 0 && <p className="platform-warning">개체별 분할에는 저장한 고정 전처리·FFT 규칙을 적용할 수 없습니다. 전처리를 비우거나 기존 시맨틱 분할 도구를 사용하세요.</p>}<button type="button" className={`${buttons} workspace-button--quiet`} disabled={busy || state.uncertain} onClick={() => {if (!busy) controller.selectTargetTask(null);}}>기존 시맨틱 분할 경로 유지</button></> : <button type="button" className={buttons} disabled={busy || state.uncertain} onClick={() => {if (!busy) controller.selectTargetTask('instance_segmentation');}}>개체별 영역 분할로 학습 · 개별 다각형 라벨 필요</button>}</div>}
          {receipt?.source_task === 'segmentation' && receipt.task === 'instance_segmentation' && <p className="workspace-description">프로젝트: 시맨틱 영역 분할 · 이번 학습 복사본: 개체별 영역 분할. 개별 다각형 라벨을 기준으로 준비했습니다.</p>}
          <button type="button" className={`${buttons} workspace-button--quiet`} disabled={busy || !onNavigate} onClick={() => navigate(3)}>전처리·FFT 편집 화면 열기</button>
          <p className="workspace-description">전처리 화면을 여는 동작은 코드를 실행하거나 학습을 시작하지 않습니다. 변경된 규칙은 새 데이터 준비에 반영하세요.</p>
          {supportedTarget && status && <div className="platform-model-settings">
            <div className="workspace-field"><label htmlFor="platform-architecture">학습할 모델</label><select id="platform-architecture" value={state.architectureId} disabled={busy || state.uncertain || !status.architectures.some(row => row.supported && row.task === trainingTask)} onChange={event => {if (!busy && !state.uncertain) controller.chooseArchitecture(event.target.value);}}>
              {!state.architectureId && <option value="">사용 가능한 모델 없음</option>}{status.architectures.filter(row => row.supported && row.task === trainingTask).map(row => <option key={row.id} value={row.id}>{row.name}{row.weights_ready === true ? ' · 가중치 준비됨':' · 가중치 필요'}</option>)}
            </select><span className="workspace-description">{licenseName ? `가중치 라이선스: ${licenseName}` : '라이선스 정보는 가중치 준비 결과에서 확인하세요.'}</span>{licenseUrl && <a className="platform-license-link" href={licenseUrl} target="_blank" rel="noopener noreferrer">라이선스 원문 열기 (새 창)</a>}</div>
            <div className="workspace-field"><label htmlFor="platform-epochs">최대 학습 횟수</label><input ref={epochRef} id="platform-epochs" type="number" inputMode="numeric" min={1} max={1000} step={1} value={state.epochs} disabled={busy || state.uncertain} aria-invalid={!!state.epochsError} aria-describedby={state.epochsError ? 'platform-epochs-error':'platform-epochs-help'} onChange={event => {if (!busy && !state.uncertain) controller.setEpochs(event.target.value);}}/><span id="platform-epochs-help" className="workspace-description">1~1,000회. 실제 학습은 일찍 끝날 수 있습니다.</span>{state.epochsError && <span id="platform-epochs-error" className="workspace-field__error" role="alert">{state.epochsError}</span>}</div>
          </div>}
          {!!status?.models.length && <button type="button" className={buttons} disabled={writeBlocked || !status.permissions.can_train || !receipt || !architecture || activeJobs.length > 0} onClick={() => {if (!writeBlocked) void (architecture?.weights_ready === true ? controller.train() : controller.weights());}}>{architecture?.weights_ready === true ? '이 설정으로 추가 학습 시작':'추가 모델 가중치 준비'}</button>}
          <details className="workspace-disclosure"><summary>기존 모델군과 전문 도구</summary><p>{status?.native_tasks.length ? status.native_tasks.map(nativeName).join(' · ') : '기존 학습 카탈로그에서 모든 모델군과 지원 조건을 확인할 수 있습니다.'}</p><button type="button" className={buttons} disabled={busy || !onNavigate} onClick={() => navigate(3)}>기존 모델·전문 도구 열기</button><p className="workspace-description">시맨틱 분할, OCR, 회전 검출, 각도 보정, 이미지 개선, GAN, 이상탐지 등은 기존 모델 경로를 유지합니다.</p></details>
        </section>
        {activeJobs.length > 0 && <section className="workspace-section" aria-labelledby="platform-jobs-title"><h3 id="platform-jobs-title">진행 중인 학습</h3><ol className="platform-record-list">{activeJobs.map(job => <JobRecord key={job.id} job={job} controller={controller} disabled={busy} canCancel={!!status?.permissions.can_train}/>)}</ol></section>}
        <section ref={resultsRef} tabIndex={-1} className="workspace-section platform-results" aria-labelledby="platform-results-title" data-testid="geti-platform-results">
          <div className="workspace-section__header"><div><h3 id="platform-results-title">저장된 모델과 결과</h3><p className="workspace-description">{status?.models.length ? `현재 프로젝트에 모델 ${count(status.models.length)}개가 있습니다.` : '아직 이 작업대에서 확인한 모델이 없습니다. 학습 완료 후 상태를 다시 확인하세요.'}</p></div></div>
          {selectedModel && <>
            <div className="platform-model-settings"><div className="workspace-field"><label htmlFor="platform-model">확인할 모델</label><select id="platform-model" value={state.modelId} disabled={busy} onChange={event => {if (!busy) controller.selectModel(event.target.value);}}>{status?.models.map(model => <option key={model.id} value={model.id}>{model.name || model.architecture} · {nativeName(model.task)}</option>)}</select></div><div className="workspace-field"><label htmlFor="platform-variant">사용할 파일 형식</label><select id="platform-variant" value={state.variantId} disabled={busy || !selectedModel.variants.length} onChange={event => {if (!busy) controller.selectVariant(event.target.value);}}>{!selectedModel.variants.length && <option value="">저장된 파일 없음</option>}{selectedModel.variants.map(row => <option key={row.id} value={row.id}>{row.format} · {row.precision}</option>)}</select></div></div>
            <h4 className="platform-subtitle">{selectedModel.name || selectedModel.architecture}</h4><p className="workspace-description">{selectedModel.architecture} · {nativeName(selectedModel.task)}</p>
            {metrics.length ? <div className="platform-table-wrap"><table><caption>저장된 평가 기록 · 분할별 수치를 확인하세요</caption><thead><tr><th scope="col">이미지 분할</th><th scope="col">지표</th><th scope="col">기록값</th></tr></thead><tbody>{metrics.slice(0,30).map((row,index) => <tr key={index}><td>{row.subset}</td><td>{row.name}</td><td>{row.value}</td></tr>)}</tbody></table>{metrics.length > 30 && <p>지표 {count(metrics.length)}개 중 앞의 30개를 표시합니다. 전체 기록은 평가 상세에서 확인하세요.</p>}</div> : <p className="platform-empty">이 형식에 저장된 평가 수치가 없습니다. 학습 완료를 독립 시험 통과로 표시하지 않습니다.</p>}
            <div className="workspace-toolbar"><button type="button" className={buttons} disabled={busy || !variant} onClick={() => {if (!busy && variant) void controller.exportModel();}}>선택 모델 내보내기</button><button type="button" className={`${buttons} workspace-button--quiet`} disabled={busy || !onNavigate} onClick={() => navigate(4)}>평가·비교 화면 열기</button><button type="button" className={`${buttons} workspace-button--quiet`} disabled={busy || !onNavigate} onClick={() => navigate(5)}>검사 흐름 화면 열기</button></div>
            <details className="workspace-disclosure"><summary>모델과 평가 기준 식별 정보</summary><dl className="platform-identities"><dt>모델</dt><dd>{selectedModel.id}</dd><dt>형식</dt><dd>{variant?.id || '없음'}</dd><dt>학습 데이터 기준</dt><dd>{selectedModel.dataset_revision_id || '기록 없음'}</dd></dl>{metrics.map((metric,index) => <p key={index}>{metric.name} · {metric.subset} · 평가 기준 {metric.revision}</p>)}</details>
            <div className="workspace-record"><h4>프로젝트 사진으로 빠른 확인</h4><p className="workspace-description">고정된 복사본과 해시가 같은 사진만 선택할 수 있습니다. 시험 사진을 먼저 표시하며, 이 한 장의 예측은 독립 시험 평가가 아닙니다.</p>
              {!state.imagesLoaded && <button type="button" className={buttons} disabled={busy || state.imageLoading || !receipt} onClick={() => {if (!busy && !state.imageLoading) void controller.loadImages();}}>{state.imageLoading ? '사진 목록을 읽는 중…':'확인할 사진 불러오기'}</button>}
              {state.imagesLoaded && <div className="workspace-field"><label htmlFor="platform-image">확인할 사진</label><select id="platform-image" value={state.imageId} disabled={busy || state.imageLoading || !state.images.length} onChange={event => {if (!busy) controller.selectImage(event.target.value);}}>{!state.images.length && <option value="">현재 페이지에 일치하는 사진 없음</option>}{state.images.map(image => <option key={image.image_uuid} value={image.image_uuid}>{image.file_name || image.relative_path}{receipt?.source_mapping.find(row => row.relative_path === image.relative_path)?.subset === 'testing' ? ' · 시험 사진':''}</option>)}</select></div>}
              {state.imageError && <p className="platform-error" role="alert">{state.imageError}</p>}
              <div className="workspace-toolbar">{(state.nextImageCursor || state.imageError) && <button type="button" className={buttons} disabled={busy || state.imageLoading} onClick={() => {if (!busy && !state.imageLoading) void controller.loadImages(!!state.nextImageCursor);}}>{state.imageError ? '사진 목록 다시 확인':'사진 더 불러오기'}</button>}{state.imagesLoaded && <button type="button" className={buttons} disabled={writeBlocked || !status?.permissions.can_train || !state.imageId || !variant || variant.prediction_supported === false} onClick={() => {if (!writeBlocked && status?.permissions.can_train && state.imageId && variant?.prediction_supported !== false) void controller.predict();}}>선택 사진 예측</button>}</div>
              {!status?.permissions.can_train && <p className="workspace-description">사진 예측은 학습 실행 권한이 있는 계정에서 할 수 있습니다. 저장된 모델과 평가는 계속 확인할 수 있습니다.</p>}
              {variant?.prediction_supported === false && <p className="workspace-description">이 형식은 여기서 사진 예측을 지원하지 않습니다. 내보내기 또는 지원 형식을 선택하세요.</p>}
              {state.prediction && <div className="platform-prediction"><strong>사진 예측 응답</strong>{predictions.length ? <ul>{predictions.map((row,index) => <li key={index}>{row.label} · {row.shape}{row.confidence === null ? '' : ` · 신뢰도 ${row.confidence.toLocaleString('ko-KR',{maximumFractionDigits:4})}`}</li>)}</ul> : <p>표시할 라벨 예측이 없습니다. 빈 응답을 정상 판정으로 간주하지 않습니다.</p>}{(state.prediction.predictions?.length ?? 0) > 100 && <p>앞의 100개 예측을 표시합니다.</p>}</div>}
            </div>
          </>}
          {recentJobs.length > 0 && <details className="workspace-disclosure"><summary>이전 학습 기록 {count(recentJobs.length)}개</summary><ol className="platform-record-list">{recentJobs.slice(0,state.jobLimit).map(job => <JobRecord key={job.id} job={job} controller={controller} disabled={busy} canCancel={false}/>)}</ol>{recentJobs.length > state.jobLimit && <button type="button" className={buttons} onClick={() => controller.showMoreJobs()}>이전 기록 더 보기</button>}</details>}
        </section>
        {receipt && <details className="workspace-section workspace-disclosure"><summary>작업 복사본 라벨을 새 버전으로 채택</summary><p>복사본의 라벨과 원본 기준을 검토한 뒤 새 라벨셋과 데이터 버전을 만듭니다. 현재 선택한 라벨셋과 이전 버전은 이 버튼으로 바뀌지 않습니다.</p>{!status?.permissions.can_adopt && <p className="workspace-description">라벨 채택은 검토 권한이 있는 계정에서 할 수 있습니다.</p>}{!receipt.revision && <p className="workspace-description">검토 기준이 기록되지 않아 이 복사본의 라벨은 채택할 수 없습니다.</p>}{state.adopted ? <><p role="status">새 라벨셋과 데이터 버전을 저장했습니다. 이전 라벨셋은 계속 보존됩니다.</p><button type="button" className={buttons} disabled={writeBlocked || !onUseAdoptedLabels} onClick={() => {if (!writeBlocked) onUseAdoptedLabels?.();}}>채택한 라벨로 라벨 화면 열기</button><details className="workspace-disclosure"><summary>채택한 버전 식별 정보</summary><dl className="platform-identities"><dt>라벨셋</dt><dd>{state.adopted.labelset_id}</dd><dt>데이터 버전</dt><dd>{state.adopted.dataset_version_id}</dd></dl></details></> : <><label className="platform-review-check"><input type="checkbox" checked={state.adoptionReview} disabled={writeBlocked || !status?.permissions.can_adopt || !receipt.revision || activeJobs.length > 0} onChange={event => {if (!writeBlocked) controller.reviewAdoption(event.target.checked);}}/>복사본 라벨을 검토했으며 새 버전으로 저장합니다.</label><button type="button" className={buttons} disabled={writeBlocked || !state.adoptionReview || !status?.permissions.can_adopt || !receipt.revision || activeJobs.length > 0} onClick={() => {if (!writeBlocked && state.adoptionReview) void controller.adoptLabels();}}>검토한 라벨을 새 버전으로 저장</button></>}</details>}
      </div>
      <aside className="workspace-section platform-next" aria-labelledby="platform-next-title">
        <p className="platform-eyebrow">다음 할 일</p><h3 id="platform-next-title">{next.label}</h3><p>{next.reason}</p>
        <div className="platform-live" role="status" aria-live="polite" aria-atomic="true">{state.loading ? '현재 프로젝트의 상태를 읽는 중…' : state.busy ? '요청 결과를 확인하는 중…' : state.notice || (status ? `학습 환경: ${{unavailable:'사용 불가',stopped:'정지',starting:'시작 중',running:'실행 중',error:'오류'}[status.runtime.state]}` : '준비 상태를 아직 읽지 못했습니다.')}</div>
        {state.error && <div className="workspace-feedback" data-kind="error" role="alert"><div><strong>요청 결과를 확인하세요</strong><p>{state.error}</p>{status && <p>이전에 읽은 정보가 표시되어 있습니다. 현재 상태를 다시 확인한 뒤 작업하세요.</p>}</div></div>}
        {state.uncertain && <p className="platform-warning">제출 응답이 유실되어 실행 여부를 확인 중입니다. 상태 확인은 새 학습을 시작하지 않습니다.</p>}
        {unconfirmed && status?.permissions.can_train && <div className="workspace-record"><strong>미확인 작업 복구</strong><p className="workspace-description">현재 프로젝트의 실행 환경과 진행 중인 작업을 종료해 남은 상태를 확인합니다. 새 학습은 제출하지 않습니다.</p><button type="button" className={buttons} disabled={busy} onClick={() => {if (!busy) void controller.recover();}}>실행 환경 종료하고 작업 상태 정리</button></div>}
        {status?.runtime.message && <p className={status.runtime.degraded || status.runtime.state === 'error' ? 'platform-warning':'workspace-description'}>{status.runtime.message}</p>}
        {status?.project.binding_message && <p className="platform-warning" role="status">{status.project.binding_message}</p>}
        {next.disabled && <p className="workspace-description">현재 계정에는 이 작업을 실행할 권한이 없습니다.</p>}
        <button type="button" className={`${buttons} workspace-button--primary`} disabled={disabled} onClick={runPrimary}>{next.label}</button>
        {next.kind !== 'refresh' && <button type="button" className={`${buttons} workspace-button--quiet`} disabled={busy} onClick={() => {if (!busy) void controller.refresh();}}>상태 새로고침</button>}
        {!status?.project.supported && status && <p className="workspace-description">Geti 지원: 사진 분류·물체 검출·개체별 분할. 기존 모델 도구는 현재 작업 종류를 유지합니다.</p>}
        <details className="workspace-disclosure"><summary>환경과 준비 정보</summary><p>{status?.runtime.message || '현재 프로젝트 전용 실행 환경을 사용합니다.'}</p><p>이 화면은 새 계정이나 외부 프로젝트를 만들도록 요청하지 않습니다. 실제 원본, 권한, 전처리와 저장 버전은 기존 모두비전 기준을 따릅니다.</p></details>
      </aside>
    </div>
  </section>;
}
