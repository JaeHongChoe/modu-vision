export type ErrorActionSettings = {batchSize: number; device?: string; preset?: string};
export function trainingPresetBatchSize(preset: string): number { return preset === 'precision' ? 8 : 16; }
type Context = {settings?: ErrorActionSettings; setSettings?: (next: Partial<ErrorActionSettings>) => void;
  selectLocal?: () => Promise<void>; readTarget?: () => string | null; openData?: () => Promise<void>;
  openSynthetic?: () => void; isCurrent?:()=>boolean};
export const errorActionLabels: Record<string, [string, string]> = {
  reduce_batch_size: ['배치 크기 절반으로 설정', 'Halve batch size'],
  continue_on_cpu: ['새 학습을 로컬 CPU로 설정', 'Use local CPU for next training'],
  rebalance_classes: ['데이터 분포 검토로 이동', 'Review class distribution'],
  open_synthetic_dialog: ['합성 데이터 준비 열기', 'Open synthetic preparation'],
};
export async function performErrorAction(action: string | undefined, context: Context): Promise<{effect: 'applied' | 'navigation' | 'unavailable'; message: string}> {
  if(context.isCurrent?.()===false)throw new Error('프로젝트가 바뀌었습니다. 현재 설정에서 다시 확인하세요.');
  if (action === 'reduce_batch_size' && context.settings && context.setSettings) {
    const before = context.settings.batchSize;
    if (!Number.isInteger(before) || before < 2) throw new Error('배치 크기를 더 줄일 수 없습니다. 데이터·장치 준비를 확인하세요.');
    const next = Math.max(1, Math.floor(before / 2)); context.setSettings({batchSize: next});
    return {effect: 'applied', message: `다음 학습 배치 ${before} → ${next}. 진행 중인 작업은 바뀌지 않습니다.`};
  }
  if (action === 'continue_on_cpu' && context.selectLocal && context.readTarget && context.setSettings) {
    await context.selectLocal();
    if(context.isCurrent?.()===false)throw new Error('프로젝트가 바뀌었습니다. 장치 변경을 적용하지 않았습니다.');
    if (context.readTarget() !== null) throw new Error('로컬 학습 위치 변경을 확인하지 못했습니다. 다시 시도하세요.');
    context.setSettings({device: 'cpu'});
    return {effect: 'applied', message: '다음 학습 위치는 이 컴퓨터, 장치는 CPU로 설정했습니다. 학습 전 준비 검사를 실행하세요.'};
  }
  if (action === 'rebalance_classes' && context.openData) {
    await context.openData(); return {effect: 'navigation', message: '데이터 분포를 검토하세요. 데이터 비율은 자동 변경하지 않습니다.'};
  }
  if (action === 'open_synthetic_dialog' && context.openSynthetic) {
    context.openSynthetic(); return {effect: 'navigation', message: '합성 데이터 준비 화면을 열었습니다. 생성 후보는 검토가 필요합니다.'};
  }
  return {effect: 'unavailable', message: '이 오류에는 자동 적용할 조치가 없습니다. 안내된 원인과 준비 상태를 확인하세요.'};
}
