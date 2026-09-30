/**
 * src/renderer/types/index.ts
 * Global TypeScript contracts and data models for Vision AI Studio frontend.
 */

export type VisionTask = 'classification' | 'detection' | 'segmentation' | 'anomaly';
export type FlowModelTask = VisionTask | 'patch_classification' | 'ocr' | 'rotated_detection' | 'enhancement';
export type TaskType = VisionTask;
export type TrainingPreset = 'fast' | 'precision';
export type Language = 'ko' | 'en';
export type Severity = 'critical' | 'high' | 'medium' | 'low';
export type ToolType =
  | 'select'
  | 'bbox'
  | 'rotated_bbox'
  | 'polygon'
  | 'brush'
  | 'eraser'
  | 'auto_select'
  | 'shape_converter';

export interface Point {
  x: number;
  y: number;
}

export interface BBox {
  xmin: number;
  ymin: number;
  xmax: number;
  ymax: number;
}

export interface RotatedBBox {
  cx: number;
  cy: number;
  width: number;
  height: number;
  angle: number; // in degrees
}

export interface ViewTransform {
  scale: number;
  offsetX: number;
  offsetY: number;
}

export interface Category {
  id: number;
  name: string;
  color: string;
}

export type HandleType = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w' | 'rot';

export interface HandlePosition {
  type: HandleType;
  x: number; // Viewport CSS px
  y: number; // Viewport CSS px
  cursor: string;
}

export type ClassSplitCounts = Record<string, { train: number; val: number; test: number }>;

export interface ImageMeta {
  image_id: string;
  file_name: string;
  file_path: string;
  width?: number;
  height?: number;
  split: string;
  label?: string | null;
  thumbnail_url: string;
  has_annotation?: boolean;
}

export interface AnnotationItem {
  id?: string;
  type: 'tag' | 'bbox' | 'rotated_bbox' | 'polygon' | 'brush_mask';
  label: string;
  category_id?: number;
  bbox?: [number, number, number, number]; // [xmin, ymin, xmax, ymax]
  rotated_bbox?: [number, number, number, number, number]; // [cx, cy, width, height, angle_degrees]
  polygon?: Array<[number, number]>;
  points?: Array<[number, number]>;
  mask_rle?: string;
  is_normal?: boolean;
  color?: string;
}

export interface AnnotationSavePayload {
  image_id: string;
  annotations: AnnotationItem[];
  image_width: number;
  image_height: number;
  output_dir?: string;
}

export interface HardwareStats {
  cpu_percent: number;
  memory_percent: number;
  gpu_name: string;
  gpu_memory_used_mb: number;
  device_type: string;
}

export interface ConfusionMatrixData {
  classes: string[];
  class_names?: string[];
  matrix: number[][];
  normalized_matrix: number[][];
  cell_samples: Record<string, string[]>;
}

export interface TestPredictionItem {
  image_id: string;
  file_name: string;
  file_path: string;
  ground_truth: string;
  predicted_class: string;
  confidence: number;
  defect_score?: number;
  is_correct: boolean;
  thumbnail_url: string;
}

export interface EvaluationResults {
  job_id: string;
  task: VisionTask;
  metrics: Record<string, any>;
  confusion_matrix: ConfusionMatrixData;
  test_predictions: TestPredictionItem[];
  evaluated_at?: string;
}

export interface ErrorCatalogItem {
  code: string;
  title_en: string;
  title_kr: string;
  title_ko?: string;
  description_en: string;
  description_kr: string;
  message_en?: string;
  message_ko?: string;
  remediation_en: string;
  remediation_kr: string;
  remediation?: string;
  severity: Severity;
  auto_fixable: boolean;
  cause_en?: string;
  cause_kr?: string;
  action?: string;
  details?: any;
}

export interface OverkillUnderkillPoint {
  threshold: number;
  underkill_count: number;
  underkill_rate: number;
  overkill_count: number;
  overkill_rate: number;
  tp: number;
  tn: number;
  total_cost: number;
}

export interface OverkillUnderkillAnalysis {
  sample_count: number;
  total_defects: number;
  total_normals: number;
  target_max_underkill: number;
  optimal_threshold: number;
  optimal_cost_threshold: number;
  current_stats: {
    threshold: number;
    underkill_count: number;
    overkill_count: number;
    total_cost: number;
  };
  optimal_stats: {
    threshold: number;
    underkill_count: number;
    overkill_count: number;
    total_cost: number;
  };
  tradeoff_curve: OverkillUnderkillPoint[];
}

export interface BenchmarkResult {
  device: string;
  device_name: string;
  iterations: number;
  mean_latency_ms: number;
  p95_latency_ms: number;
  min_latency_ms: number;
  max_latency_ms?: number;
  std_latency_ms?: number;
  fps: number;
  resolution: string;
  batch_size?: number;
}

export type PortType = 'image' | 'trigger' | 'mask' | 'data' | 'pass' | 'fail' | 'plc';

export interface NodePort {
  id: string;
  name: string;
  type: PortType;
  direction: 'in' | 'out';
  label: string;
  pinNumber: number;
}

export interface FlowNodeData {
  label: string;
  node_type: 'input' | 'fixed_roi' | 'patch_split' | 'preprocess' | 'detection_crop' | 'inspection' | 'blob_measure' | 'aggregate' | 'decision' | 'output';
  task?: string;
  model_job_id?: string;
  threshold?: number;
  crop_padding?: number;
  rule?: string;
  params?: Record<string, any>;
  ports?: {
    inputs: NodePort[];
    outputs: NodePort[];
  };
}

export interface FlowNode {
  id: string;
  type?: string;
  position: { x: number; y: number };
  data: FlowNodeData;
}

export interface FlowEdge {
  id: string;
  source: string;
  target: string;
  sourcePort?: string;
  targetPort?: string;
  label?: string;
  isBranch?: 'pass' | 'fail' | 'review' | 'default';
  payload_type?: 'image' | 'roi' | 'result';
  predicate?: { kind: 'class'; operator: 'present' | 'absent'; class_name: string; min_confidence?: number } | null;
}

export interface FlowchartPipeline {
  id: string;
  name: string;
  description?: string;
  nodes: FlowNode[];
  edges: FlowEdge[];
}

export interface FlowchartCrop {
  roi_id: string;
  source_node_id?: string;
  label: string;
  bbox: [number, number, number, number] | number[];
  defect_score: number;
  verdict: 'OK' | 'NG';
  crop_thumbnail: string;
  flaw_type: string;
  confidence?: number;
  recognized_text?: string;
  predicted_class?: string;
  polygon?: number[][];
  mask?: string;
  anomaly_map?: string;
  map_semantics?: 'patch_score' | 'pixel_score' | 'token_explanation';
  anomaly_values?: { dtype: string; encoding: string; shape: number[]; data: string };
  source_transform?: number[][];
  defect_area_px?: number;
  blob_count?: number;
  largest_blob_area_px?: number;
}

export interface FlowchartExecutionStep {
  node_id: string;
  name: string;
  status: 'pending' | 'running' | 'passed' | 'flagged_ng' | 'error' | 'skipped' | 'review_required' | 'warning_untrained';
  latency_ms: number;
  input_payload_type?: 'image' | 'roi' | 'result' | null;
  output_payload_type?: 'image' | 'roi' | 'result' | null;
  input_count?: number | null;
  output_count?: number | null;
  branch_verdict?: 'OK' | 'NG' | 'REVIEW' | null;
  selected_edge_ids?: string[];
  skip_reason?: string | null;
  artifacts?: Array<{ roi_id: string; bbox: number[]; image: string; mask?: string; source_transform?: number[][]; image_size?: number[]; evidence?: Record<string, unknown> }>;
}

export interface FlowchartExecutionResult {
  status: string;
  final_verdict: 'OK' | 'NG' | 'REVIEW';
  is_ok: boolean;
  rejection_reason: string;
  roi_count: number;
  defective_roi_count: number;
  crops: FlowchartCrop[];
  annotated_image?: string;
  execution_steps: FlowchartExecutionStep[];
  total_latency_ms: number;
  inspected_image_size?: number[];
  tiles_processed?: number;
  preview_max_dim_px?: number;
  image_path?: string;
  image_id?: string;
  routed_output_node_id?: string;
  error_message?: string;
  execution_target?: 'local' | 'selected_compute' | 'model_compute';
  execution_device?: string;
  compute_profile_id?: string | null;
  compute_profile_name?: string;
}

export interface SelectedInspectionImage {
  source: 'dataset' | 'file' | 'preset' | 'operational';
  imagePath: string;
  imageId?: string;
  fileName: string;
  thumbnailUrl?: string;
}

export interface RuntimeExportResult {
  status: string;
  package_name: string;
  package_path: string;
  manifest: Array<{ name: string; size_kb: number }>;
  total_files: number;
}
