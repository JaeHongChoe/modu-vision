/** Where the six specialist families live, said in steps 1, 2 and 4 (audit F-16). They are not project types: their
 *  data is prepared, trained and evaluated in step 3's workbenches, and step 4's evaluation covers the four core tasks. */
import { useProjectStore } from '../../stores/useProjectStore';

const TEXT: Record<1 | 2 | 4, string> = {
  1: 'OCR·회전 검출·패치 분류·회전 보정·이미지 개선·결함 이미지 생성은 프로젝트 종류가 아닙니다. 이 프로젝트의 원본 이미지로 3단계 각 작업대에서 정답을 만들고 학습합니다.',
  2: '여기서 그린 회전 박스는 3단계 회전 검출 작업대의 "2단계에서 그린 회전 박스 가져오기"로 학습에 쓸 수 있습니다. OCR 글자 정답은 3단계 OCR 작업대에서 입력합니다.',
  4: '이 화면은 분류·검출·분할·이상 탐지 모델을 평가합니다. OCR·회전 검출 등 다른 계열은 3단계 각 작업대 안의 평가로 확인하세요.',
};

export function SpecialistFamilyNote({ step }: { step: 1 | 2 | 4 }) {
  const setStep = useProjectStore(state => state.setStep);
  return <p role="note" className="rounded border border-slate-700 bg-[#101A28] px-3 py-2 text-xs text-slate-300">
    {TEXT[step]} <button type="button" className="ml-1 text-cyan-200 underline" onClick={() => void setStep(3)}>3단계 작업대로</button>
  </p>;
}
