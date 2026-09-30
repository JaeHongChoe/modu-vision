import type { VisionTask } from '../../types';

export const modelChoices: Record<VisionTask, Array<{value: string; label: string}>> = {
  classification: [{value: 'dinov3_vits16', label: 'DINOv3 ViT-S/16 · 기본'}, {value: 'dinov3_vitb16', label: 'DINOv3 ViT-B/16'}, {value: 'resnet18', label: 'ResNet18 · 기존 구조'}, {value: 'convnext_tiny', label: 'ConvNeXt Tiny · 기존 구조'}, {value: 'efficientnet_b0', label: 'EfficientNet B0 · 기존 구조'}],
  segmentation: [{value: 'dinov3_vits16', label: 'DINOv3 ViT-S/16 · 기본'}, {value: 'dinov3_vitb16', label: 'DINOv3 ViT-B/16'}, {value: 'unet', label: 'UNet · 기존 구조'}],
  detection: [{value: 'yolo26n', label: 'YOLO26 nano · 기본'}, {value: 'yolo26s', label: 'YOLO26 small'}, {value: 'fasterrcnn', label: 'Faster R-CNN · 기존 구조'}],
  anomaly: [{value: 'padim', label: 'PaDiM'}, {value: 'patchcore', label: 'PatchCore'}],
};

export function trainingModelOverrides(task: VisionTask, model: string, checkpoint = ''): Record<string, unknown> {
  if (!modelChoices[task].some(choice => choice.value === model)) throw new Error('현재 검사 종류와 맞는 모델을 선택하세요.');
  const options: Record<string, unknown> = task === 'segmentation' ? {model_name: model} : task === 'anomaly' ? {anomaly_method: model} : {backbone: model};
  if (checkpoint.trim()) options.pretrained_checkpoint = checkpoint.trim();
  return options;
}
