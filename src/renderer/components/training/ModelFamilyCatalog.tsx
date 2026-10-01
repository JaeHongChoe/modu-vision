import { useEffect, useState } from 'react';
import { Boxes } from 'lucide-react';
import { request } from '../../services/api';
import type { ModelFamily } from '../../services/modelTrainingProgram';

type Method = {method: string; architectures: string[]; continuation: string; prerequisite: string; missing_dependencies: string[]};
type Family = {task: ModelFamily; label: string; model: string; architectures: string[]; devices: string[]; default_architecture: string; prerequisite: string; remote_training: boolean; continuation: string; stages: string[]; missing_dependencies: string[]; methods?: Method[]};
const stageNames: Record<string, string> = {label: '정답 준비', train: '학습', evaluate: '평가', flow: '플로우 검사', generate: '생성', review: '검토·채택', export: '내보내기'};

export function ModelFamilyCatalog({selectedFamily, onSelect}: {selectedFamily?: ModelFamily; onSelect?: (family: ModelFamily) => void}) {
  const [families, setFamilies] = useState<Family[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    let current = true;
    request<{families: Family[]}>('/api/models/capabilities').then(result => {
      if (current) setFamilies(result.families);
    }).catch(cause => { if (current) setError(String(cause.message || cause)); });
    return () => { current = false; };
  }, []);
  return <section className="rounded-xl border border-[#344255] bg-[#131D2B] p-4 text-xs text-slate-200" aria-label="모델 학습 허브">
    <h2 className="flex items-center gap-2 text-sm font-semibold"><Boxes className="h-4 w-4 text-cyan-300" />모델 학습 허브</h2>
    <p className="mt-2 text-slate-400">검사 목적에 맞는 모델을 선택하고 정답 준비부터 완료 후보의 평가·배포까지 진행하세요.</p>
    {onSelect && <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">{families.map(family => <button type="button" key={family.task} onClick={() => onSelect(family.task)} aria-pressed={selectedFamily === family.task}
      className={`rounded-lg border p-3 text-left transition-colors ${selectedFamily === family.task ? 'border-cyan-500 bg-cyan-950/40' : 'border-[#314155] bg-[#0D1622] hover:border-slate-500'}`}>
      <span className="block font-semibold text-white">{family.label}</span><span className="mt-1 block text-[11px] text-slate-400">{family.model}</span>
    </button>)}</div>}
    <details className="mt-4"><summary className="cursor-pointer text-slate-300">{onSelect ? '선택 모델의 데이터·장치·학습 구조' : '모델 종류와 필요한 데이터'}</summary>
    <div className="mt-3">
      <p className="mb-3 text-slate-300">분류·패치·분할은 DINOv3, 객체 검출은 YOLO를 기본으로 사용합니다. DINOv3 인코더는 고정하고 검사 헤드를 현재 라벨로 학습합니다. YOLO는 선택한 학습 설정으로 학습합니다. 다중 GPU DDP는 모델을 각 GPU에 복제하고 데이터 배치를 나눕니다. 모델 분할·샤딩은 제공하지 않습니다.</p>
      {error && <p role="alert" className="text-amber-300">모델 종류 확인 실패: {error}</p>}
      <div className="grid gap-2">{families.filter(family => !onSelect || !selectedFamily || family.task === selectedFamily).map(family => <article key={family.task} className="rounded border border-[#314155] bg-[#0D1622] p-3">
        <h3 className="font-semibold text-white">{family.label}<span className="ml-2 font-normal text-cyan-300">{family.model}</span></h3>
        <p className="mt-2 leading-5 text-slate-300">{family.prerequisite}</p>
        <p className="mt-2 break-words text-slate-400">구조: {family.architectures.join(' · ')} · 기본 {family.default_architecture}</p>
        <p className="mt-2 text-slate-400">{family.remote_training ? '로컬·서버 학습' : '로컬 학습'} · {family.devices.map(device => device.toUpperCase()).join(' / ')}</p>
        <p className="mt-1 text-slate-400">{family.stages.map(stage => stageNames[stage] || stage).join(' → ')}</p>
        {!family.methods && family.continuation === 'statistical_refit' && <p className="mt-1 text-slate-400">검증된 부모 특징 추출기로 정상 통계를 다시 구성합니다.</p>}
        {family.methods?.map(method => <div key={method.method} className="mt-2 border-t border-[#314155] pt-2">
          <div className="font-semibold text-cyan-200">{method.method === 'dino_synthetic' ? 'DINOv3 · 합성 결함 학습' : method.method === 'padim' ? 'PaDiM' : 'PatchCore'}</div>
          <p className="mt-1 text-slate-300">{method.prerequisite}</p>
          <p className="mt-1 text-slate-400">{method.continuation === 'statistical_refit' ? '검증된 부모 특징 추출기로 정상 통계를 다시 구성합니다.' : '같은 백본·패치 조건의 부모 전체 가중치에서 다시 학습합니다.'}</p>
          <p className="mt-1 break-words text-slate-400">구조: {method.architectures.join(' · ')}</p>
          {!!method.missing_dependencies.length && <p className="mt-1 text-amber-300">추가 설치 필요: {method.missing_dependencies.join(', ')}</p>}
        </div>)}
        {!!family.missing_dependencies.length && <p className="mt-2 text-amber-300">추가 설치 필요: {family.missing_dependencies.join(', ')}</p>}
      </article>)}</div>
    </div></details>
    {error && <p role="alert" className="mt-3 text-amber-300">모델 종류 확인 실패: {error}</p>}
  </section>;
}
