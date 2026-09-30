import type { VisionTask } from '../../types';

export const modelChoices: Record<VisionTask, Array<{value: string; label: string}>> = {
  classification: [{value: 'dinov3_vits16', label: 'DINOv3 ViT-S/16 · 기본'}, {value: 'dinov3_vitb16', label: 'DINOv3 ViT-B/16'}, {value: 'resnet18', label: 'ResNet18 · 기존 구조'}, {value: 'convnext_tiny', label: 'ConvNeXt Tiny · 기존 구조'}, {value: 'efficientnet_b0', label: 'EfficientNet B0 · 기존 구조'}],
  segmentation: [{value: 'dinov3_vits16', label: 'DINOv3 ViT-S/16 · 기본'}, {value: 'dinov3_vitb16', label: 'DINOv3 ViT-B/16'}, {value: 'unet', label: 'UNet · 기존 구조'}],
  detection: [{value: 'yolo26n', label: 'YOLO26 nano · 기본'}, {value: 'yolo26s', label: 'YOLO26 small'}, {value: 'fasterrcnn', label: 'Faster R-CNN · 기존 구조'}],
  anomaly: [{value: 'padim', label: 'PaDiM'}, {value: 'patchcore', label: 'PatchCore'}, {value: 'dino_synthetic', label: 'DINOv3 · 합성 결함 학습'}],
};

export type DinoSyntheticTrainingOptions = {
  anomaly_backbone: string;
  patch_size: number;
  stride: number;
  patches_per_image: number;
  inference_batch_size: number;
};

export const dinoSyntheticDefaults: DinoSyntheticTrainingOptions = {
  anomaly_backbone: 'dinov3_vits16', patch_size: 256, stride: 128,
  patches_per_image: 8, inference_batch_size: 32,
};

export function trainingModelOverrides(task: VisionTask, model: string, checkpoint = '',
  syntheticOptions: Partial<DinoSyntheticTrainingOptions> = {}): Record<string, unknown> {
  if (!modelChoices[task].some(choice => choice.value === model)) throw new Error('현재 검사 종류와 맞는 모델을 선택하세요.');
  const options: Record<string, unknown> = task === 'segmentation' ? {model_name: model} : task === 'anomaly' ? {anomaly_method: model} : {backbone: model};
  if (task === 'anomaly' && model === 'dino_synthetic') {
    const selected = {...dinoSyntheticDefaults, ...syntheticOptions};
    if (!['dinov3_vits16', 'dinov3_vitb16', 'dinov3_vitl16'].includes(selected.anomaly_backbone)) throw new Error('지원하는 DINOv3 백본을 선택하세요.');
    if (!Number.isInteger(selected.patch_size) || selected.patch_size < 32 || selected.patch_size > 1024 || selected.patch_size % 16) throw new Error('원본 패치 크기는 32~1024 범위의 16 배수여야 합니다.');
    if (!Number.isInteger(selected.stride) || selected.stride < 1 || selected.stride > selected.patch_size) throw new Error('패치 간격은 1 이상이며 패치 크기 이하여야 합니다.');
    if (!Number.isInteger(selected.patches_per_image) || selected.patches_per_image < 1 || selected.patches_per_image > 128) throw new Error('원본당 학습 패치 수는 1~128 범위여야 합니다.');
    if (!Number.isInteger(selected.inference_batch_size) || selected.inference_batch_size < 1 || selected.inference_batch_size > 128) throw new Error('추론 배치는 1~128 범위여야 합니다.');
    Object.assign(options, selected);
  }
  if (checkpoint.trim()) options.pretrained_checkpoint = checkpoint.trim();
  return options;
}
