import { useEffect, useState } from 'react';
import { Boxes } from 'lucide-react';
import { request } from '../../services/api';

type Method = {method: string; architectures: string[]; continuation: string; prerequisite: string; missing_dependencies: string[]};
type Family = {task: string; label: string; model: string; architectures: string[]; devices: string[]; default_architecture: string; prerequisite: string; remote_training: boolean; continuation: string; stages: string[]; missing_dependencies: string[]; methods?: Method[]};
const stageNames: Record<string, string> = {label: '정답 준비', train: '학습', evaluate: '평가', flow: '플로우 검사', generate: '생성', review: '검토·채택', export: '내보내기'};

export function ModelFamilyCatalog() {
  const [families, setFamilies] = useState<Family[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    let current = true;
    request<{families: Family[]}>('/api/models/capabilities').then(result => {
      if (current) setFamilies(result.families);
    }).catch(cause => { if (current) setError(String(cause.message || cause)); });
    return () => { current = false; };
  }, []);
  return <details className="rounded border border-[#344255] bg-[#131D2B] text-xs text-slate-200">
    <summary className="flex cursor-pointer items-center gap-2 p-3 font-semibold"><Boxes className="h-4 w-4 text-cyan-300" />모델 종류와 필요한 데이터</summary>
    <div className="border-t border-[#344255] p-3">
      <p className="mb-3 text-slate-300">분류·분할은 DINOv3, 객체 검출은 YOLO를 기본으로 사용합니다. 기존 모델의 구조와 가중치는 그대로 읽습니다. 사전학습 백본 뒤의 검사 헤드는 현재 라벨로 학습해야 합니다.</p>
      {error && <p role="alert" className="text-amber-300">모델 종류 확인 실패: {error}</p>}
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{families.map(family => <article key={family.task} className="rounded border border-[#314155] bg-[#0D1622] p-3">
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
    </div>
  </details>;
}
