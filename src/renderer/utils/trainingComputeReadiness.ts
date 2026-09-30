import type {ComputeProbeResult} from '../services/api';
import type {TrainingPreset, VisionTask} from '../types';

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

/** Mirror the worker's selected-model gate; general runtime availability is separate. */
export function trainingComputeReadiness(probe: ComputeProbeResult | undefined | null,
  task: VisionTask, preset: TrainingPreset, options: Record<string, unknown> = {}, warmStart = false): {ready: boolean; reason: string} {
  if (!probe || !(probe.runtime_ready ?? probe.ready)) return {ready: false, reason: probe?.message || '서버 연결 검사를 완료하세요.'};
  const checks = record(probe.checks), dependencies = record(checks.runtime_dependencies);
  const model = String(task === 'segmentation' ? options.model_name || 'dinov3_vits16'
    : task === 'anomaly' ? options.anomaly_method === 'dino_synthetic' ? options.anomaly_backbone || 'dinov3_vits16' : 'resnet18'
      : options.backbone || (task === 'detection' ? 'yolo26n' : 'dinov3_vits16'));
  const required = model.startsWith('dinov3_') ? ['timm', 'safetensors', 'huggingface_hub'] : model.startsWith('yolo') ? ['ultralytics'] : [];
  const missing = required.filter(name => dependencies[name] !== true);
  if (missing.length) return {ready: false, reason: `선택한 모델에 필요한 서버 패키지: ${missing.join(', ')}`};
  if (required.length && record(checks.model_dependencies)[model] !== true)
    return {ready: false, reason: `서버에서 ${model}을 지원하는 버전이 필요합니다. timm/ultralytics를 확인한 뒤 연결을 다시 검사하세요.`};
  if (!required.length && !warmStart && options.pretrained !== false && model !== 'unet') {
    const weights = record(checks.pretrained_weights);
    const key = model === 'fasterrcnn' ? preset === 'precision' ? 'fasterrcnn_resnet50_fpn_v2' : 'fasterrcnn_mobilenet_v3_large_fpn' : model;
    if (record(weights[key]).ok !== true) return {ready: false, reason: `서버의 ${key} 사전학습 가중치를 준비한 뒤 연결을 다시 검사하세요.`};
  }
  return {ready: true, reason: '선택 모델 학습 준비 완료'};
}
