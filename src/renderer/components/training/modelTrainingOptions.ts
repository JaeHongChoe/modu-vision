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

export type DinoFineTuneOptions = {train_mode: 'head_only' | 'partial' | 'full'; partial_blocks?: number};

export function manualTrainingRecipe(epochs: string, imageSize: string): Record<string, number> {
  const recipe: Record<string, number> = {};
  if (epochs.trim()) {
    const value = Number(epochs);
    if (!Number.isInteger(value) || value < 1 || value > 500) throw new Error('학습 횟수는 1~500의 정수여야 합니다.');
    recipe.epochs = value;
  }
  if (imageSize.trim()) {
    const value = Number(imageSize);
    if (!Number.isInteger(value) || value < 64 || value > 1024 || value % 16) throw new Error('입력 크기는 64~1024 범위의 16 배수여야 합니다.');
    recipe.image_size = value;
  }
  return recipe;
}

export function trainingModelOverrides(task: VisionTask, model: string, checkpoint = '',
  syntheticOptions: Partial<DinoSyntheticTrainingOptions> = {}, purpose: 'image' | 'region' = 'image', fineTune?: DinoFineTuneOptions): Record<string, unknown> {
  if (!modelChoices[task].some(choice => choice.value === model)) throw new Error('현재 검사 종류와 맞는 모델을 선택하세요.');
  const options: Record<string, unknown> = task === 'segmentation' ? {model_name: model} : task === 'anomaly' ? {anomaly_method: model} : {backbone: model};
  if (task === 'anomaly') options.anomaly_mode = purpose === 'region' ? 'segmentation' : 'classification';
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
  if (fineTune) {
    if (!model.startsWith('dinov3') || !['classification','segmentation'].includes(task)) throw new Error('DINO 분류·분할 모델에서 학습 범위를 선택하세요.');
    if (!['head_only','partial','full'].includes(fineTune.train_mode)) throw new Error('DINO 학습 범위를 확인하세요.');
    options.train_mode = fineTune.train_mode;
    if (fineTune.train_mode === 'partial') {
      const blocks = fineTune.partial_blocks ?? 2;
      if (!Number.isInteger(blocks) || blocks < 1 || blocks > 12) throw new Error('마지막 학습 블록 수는 1~12 범위여야 합니다.');
      options.partial_blocks = blocks;
    }
  }
  return options;
}
