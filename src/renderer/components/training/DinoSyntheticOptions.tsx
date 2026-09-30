import type {DinoSyntheticTrainingOptions} from './modelTrainingOptions';

export function DinoSyntheticOptions({options, onChange, disabled}: {
  options: DinoSyntheticTrainingOptions;
  onChange: (value: DinoSyntheticTrainingOptions) => void;
  disabled: boolean;
}) {
  const numeric = (key: keyof Omit<DinoSyntheticTrainingOptions, 'anomaly_backbone'>,
    label: string, minimum: number, maximum: number, step = 1) => <label key={key} className="block text-slate-300">{label}
    <input aria-label={label} type="number" min={minimum} max={maximum} step={step} value={options[key]}
      disabled={disabled} onChange={event => onChange({...options, [key]: Number(event.target.value)})}
      className="mt-1 block w-full rounded border border-[#415970] bg-[#0B1520] px-2 py-2" />
  </label>;
  return <div className="mt-3 space-y-3">
    <p className="text-slate-300">정상 원본에서 합성 결함 패치를 만들어 DINOv3 분류 헤드를 학습합니다. 실제 NG 이미지는 평가에만 사용합니다. 패치 점수 맵은 픽셀 정답 마스크가 아닙니다.</p>
    <label className="block text-slate-300">DINOv3 백본
      <select aria-label="합성 결함 DINOv3 백본" value={options.anomaly_backbone} disabled={disabled}
        onChange={event => onChange({...options, anomaly_backbone: event.target.value})}
        className="mt-1 block w-full rounded border border-[#415970] bg-[#0B1520] px-2 py-2">
        <option value="dinov3_vits16">ViT-S/16 · 기본</option>
        <option value="dinov3_vitb16">ViT-B/16</option>
        <option value="dinov3_vitl16">ViT-L/16</option>
      </select>
    </label>
    <div className="grid grid-cols-2 gap-3">
      {numeric('patch_size', '원본 패치 크기', 32, 1024, 16)}
      {numeric('stride', '패치 간격', 1, options.patch_size)}
      {numeric('patches_per_image', '원본당 학습 패치 수', 1, 128)}
      {numeric('inference_batch_size', '추론 배치 크기', 1, 128)}
    </div>
    <p className="text-slate-400">크기와 간격은 원본 픽셀 기준입니다. 전체 이미지를 축소해서 패치를 만들지 않습니다. 패치 크기보다 작은 원본은 사용할 수 없습니다.</p>
  </div>;
}
