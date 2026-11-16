import { useEffect, useId, useRef, useState } from 'react';
import { Boxes } from 'lucide-react';
import { request } from '../../services/api';
import type { ModelFamily } from '../../services/modelTrainingProgram';
import { WorkerSupportPanel } from './WorkerSupportPanel';
import { useProjectStore } from '../../stores/useProjectStore';

type Method = {method: string; architectures: string[]; continuation: string; prerequisite: string; missing_dependencies: string[]};
type Family = {task: ModelFamily; label: string; model: string; architectures: string[]; devices: string[]; default_architecture: string; prerequisite: string; remote_training: boolean; continuation: string; stages: string[]; missing_dependencies: string[]; methods?: Method[]; execution?: {native_recipe_stages: string[]; generation_only: boolean}};
const stageNames: Record<string, string> = {label: '정답 준비', train: '학습', evaluate: '평가', predict: '단일 예측', benchmark: '모델 forward 계측', flow: '플로우 검사', generate: '생성', review: '검토·채택', export: '내보내기'};

const resultNames: Record<ModelFamily, string> = {
  classification: '이미지별 클래스', segmentation: '영역별 클래스 마스크', detection: '객체 박스와 클래스',
  anomaly: '이상 점수와 위치 맵', patch_classification: '패치별 클래스', ocr: '인식한 문자열',
  rotated_detection: '회전 박스와 클래스', defect_gan: '검토할 결함 이미지 후보',
  enhancement: '개선한 이미지', rotation: '정방향 보정각',
};

function SelectedFamilySummary({family, simple}: {family: Family; simple: boolean}) {
  return <div className="workspace-record mt-3 text-sm" aria-label="선택 모델 안내">
    <p className="font-semibold">{family.label} · 결과: {resultNames[family.task]}</p>
    <p className="mt-1">필요한 자료: {simple ? family.prerequisite.replaceAll('train/val/test', '학습·검증·시험 사진') : family.prerequisite}</p>
    <p className="mt-1">{family.remote_training ? '로컬·서버 학습' : '로컬 학습'} · 이 컴퓨터: {family.devices.length ? family.devices.map(device => device.toUpperCase()).join(' / ') : '사용 가능한 장치 없음'}</p>
    {!simple && <p className="mt-1">개별 실행: {family.execution?.native_recipe_stages.length ? family.execution.native_recipe_stages.map(stage => stageNames[stage] || stage).join(' · ') : '지원 정보 없음'}</p>}
    {!!family.missing_dependencies.length && <p className="mt-1" style={{color: 'var(--workspace-warning)'}}>추가 설치 필요: {family.missing_dependencies.join(', ')}</p>}
    {family.methods?.filter(method => method.missing_dependencies.length).map(method => <p key={method.method} className="mt-1" style={{color: 'var(--workspace-warning)'}}>{method.method} 추가 설치 필요: {method.missing_dependencies.join(', ')}</p>)}
    {family.execution?.generation_only && <p className="mt-1" style={{color: 'var(--workspace-warning)'}}>생성 후보는 검토 후 학습 데이터에 채택합니다. 검사 플로우 모델로 사용할 수 없습니다.</p>}
  </div>;
}

function FamilyCards({families, selectedFamily, onSelect, disabled, name, simple}: {
  families: Family[]; selectedFamily?: ModelFamily; onSelect: (family: ModelFamily) => void;
  disabled: boolean; name: string; simple: boolean;
}) {
  return <fieldset className="training-family-catalog__choices">
    <legend className="sr-only">검사 목적에 맞는 모델 종류 선택</legend>
    {families.map(family => <label key={family.task} className="training-family-catalog__choice"
      data-selected={selectedFamily === family.task} data-disabled={disabled}>
      <input type="radio" name={name} value={family.task} checked={selectedFamily === family.task} disabled={disabled}
        onChange={event => { if (!disabled && event.currentTarget.checked) onSelect(family.task); }} />
      <span className="training-family-catalog__choice-content">
        <span className="training-family-catalog__choice-heading"><strong>{family.label}</strong>
          {selectedFamily === family.task && <span className="training-family-catalog__selected">선택됨</span>}</span>
        <span className="training-family-catalog__purpose">결과: {resultNames[family.task]}</span>
        <span className="training-family-catalog__prerequisite">필요한 자료: {simple ? family.prerequisite.replaceAll('train/val/test', '학습·검증·시험 사진') : family.prerequisite}</span>
        <span className="training-family-catalog__architecture">{family.model}</span>
        {!!family.missing_dependencies.length && <span className="training-family-catalog__warning">추가 설치 필요: {family.missing_dependencies.join(', ')}</span>}
        {family.execution?.generation_only && <span className="training-family-catalog__warning">생성 후보 검토용 · 검사 플로우 모델로 사용할 수 없습니다.</span>}
      </span>
    </label>)}
  </fieldset>;
}

export function ModelFamilyCatalog({selectedFamily, onSelect, disabled=false}: {selectedFamily?: ModelFamily; onSelect?: (family: ModelFamily) => void; disabled?: boolean}) {
  const [families, setFamilies] = useState<Family[]>([]);
  // The page header already says what step 3 is for, so the simple view names this card by what it asks for.
  const simpleView = useProjectStore(state => state.viewMode) === 'simple';
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const searchRef = useRef<HTMLInputElement>(null);
  const radioName = useId();
  const selected = families.find(family => family.task === selectedFamily);
  const terms = query.trim().toLocaleLowerCase('ko-KR');
  const filtered = families.filter(family => [family.label, family.model, family.prerequisite, resultNames[family.task], ...family.architectures]
    .join(' ').toLocaleLowerCase('ko-KR').includes(terms));
  useEffect(() => {
    let current = true;
    request<{families: Family[]}>('/api/models/capabilities').then(result => {
      if (current) setFamilies(result.families);
    }).catch(cause => { if (current) setError(String(cause.message || cause)); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, []);
  return <section className="training-family-catalog text-sm" aria-label="모델 학습 허브">
    <h2 className="flex items-center gap-2 font-semibold"><Boxes className="h-4 w-4" />{simpleView ? '어떤 검사를 만들까요?' : '모델 학습 허브'}</h2>
    <p className="training-family-catalog__intro">필요한 결과와 준비한 자료에 맞춰 모델 종류를 선택하세요. 선택만으로 학습을 시작하지 않습니다.</p>
    {loading ? <p role="status" className="mt-3">모델 종류를 확인 중입니다. 실행 지원은 아직 확인되지 않았습니다.</p>
      : error ? <p role="alert" className="mt-3 text-amber-300">모델 종류 확인 실패: {error}</p>
      : selected ? <SelectedFamilySummary family={selected} simple={simpleView} />
      : selectedFamily ? <p role="status" className="mt-3">선택 모델의 지원 정보를 확인할 수 없습니다.</p> : null}
    {onSelect && !loading && !error && <details className="modu-stage-disclosure training-family-catalog__picker" open={!simpleView || !selected}>
      <summary>{selected ? '모델 종류 바꾸기' : '모델 종류 선택'} · {families.length.toLocaleString('ko-KR')}종</summary>
      <div className="training-family-catalog__picker-content">
      <div className="training-family-catalog__search">
        <label className="workspace-field">모델 찾기<input ref={searchRef} type="search" value={query}
          placeholder="검사 목적, 필요한 자료, 모델 이름" onChange={event => setQuery(event.currentTarget.value)} /></label>
        {query && <button type="button" className="workspace-button workspace-button--quiet" aria-label="모델 검색 지우기"
          onClick={() => { setQuery(''); searchRef.current?.focus(); }}>검색 지우기</button>}
      </div>
      <p className="training-family-catalog__count" role="status">모델 종류 {filtered.length.toLocaleString('ko-KR')}개{terms ? ` · 전체 ${families.length.toLocaleString('ko-KR')}개` : ''}</p>
      {filtered.length ? <FamilyCards families={filtered} selectedFamily={selectedFamily} onSelect={onSelect}
        disabled={disabled} name={radioName} simple={simpleView} /> : <p className="workspace-record" role="status">검색에 맞는 모델이 없습니다. 다른 검사 목적이나 자료 이름으로 찾아보세요.</p>}
      </div>
    </details>}
    {disabled&&<p role="status" className="mt-2">현재 작업을 확인 중이거나 진행 중입니다. 완료 후 모델을 선택하세요.</p>}
    <details className="mt-4"><summary className="cursor-pointer text-slate-300">{onSelect ? '선택 모델의 데이터·장치·학습 구조' : '모델 종류와 필요한 데이터'}</summary>
    <div className="mt-3">
      <p className="mb-3 text-slate-300">분류·패치·분할은 DINOv3, 객체 검출은 YOLO를 기본으로 사용합니다. DINOv3 인코더는 고정하고 검사 헤드를 현재 라벨로 학습합니다. YOLO는 선택한 학습 설정으로 학습합니다. 다중 GPU DDP는 모델을 각 GPU에 복제하고 데이터 배치를 나눕니다. 모델 분할·샤딩은 제공하지 않습니다.</p>
      <div className="grid gap-2">{families.filter(family => !onSelect || !selectedFamily || family.task === selectedFamily).map(family => <article key={family.task} className="rounded border border-[#314155] bg-[#0D1622] p-3">
        <h3 className="font-semibold text-white">{family.label}<span className="ml-2 font-normal text-cyan-300">{family.model}</span></h3>
        <p className="mt-2 leading-5 text-slate-300">{family.prerequisite}</p>
        <p className="mt-2 break-words text-slate-400">구조: {family.architectures.join(' · ')} · 기본 {family.default_architecture}</p>
        <p className="mt-2 text-slate-400">{family.remote_training ? '로컬·서버 학습' : '로컬 학습'} · 이 컴퓨터 {family.devices.map(device => device.toUpperCase()).join(' / ')}</p>
        <p className="mt-1 text-slate-400">{family.stages.map(stage => stageNames[stage] || stage).join(' → ')}</p>
        {family.execution && <div aria-label={`${family.label} 실행 위치 지원`} className="mt-2 text-slate-300">
          <p>개별 실행: {family.execution.native_recipe_stages.map(stage => stageNames[stage] || stage).join(' · ')}</p>
          {family.execution.generation_only && <p className="mt-1 text-amber-300">생성 후보는 검토 후 학습 데이터에 채택합니다. 검사 플로우 모델로 사용할 수 없습니다.</p>}
        </div>}
        {!family.methods && family.continuation === 'statistical_refit' && <p className="mt-1 text-slate-400">검증된 부모 특징 추출기로 정상 통계를 다시 구성합니다.</p>}
        {family.methods?.map(method => <div key={method.method} className="mt-2 border-t border-[#314155] pt-2">
          <div className="font-semibold text-cyan-200">{method.method === 'dino_synthetic' ? 'DINOv3 · 합성 결함 학습' : method.method === 'padim' ? 'PaDiM' : 'PatchCore'}</div>
          <p className="mt-1 text-slate-300">{method.prerequisite}</p>
          <p className="mt-1 text-slate-400">{method.continuation === 'statistical_refit' ? '검증된 부모 특징 추출기로 정상 통계를 다시 구성합니다.' : '같은 백본·패치 조건의 부모 전체 가중치에서 다시 학습합니다.'}</p>
          <p className="mt-1 break-words text-slate-400">구조: {method.architectures.join(' · ')}</p>
          {!!method.missing_dependencies.length && <p className="mt-1 text-amber-300">추가 설치 필요: {method.missing_dependencies.join(', ')}</p>}
        </div>)}
        {!!family.missing_dependencies.length && <p className="mt-2 text-amber-300">추가 설치 필요: {family.missing_dependencies.join(', ')}</p>}
        {onSelect && selectedFamily === family.task && <WorkerSupportPanel task={family.task} />}
      </article>)}</div>
    </div></details>
  </section>;
}
