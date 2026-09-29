/**
 * src/renderer/services/api.ts
 * Type-safe HTTP REST client for Python FastAPI backend with dynamic port resolution.
 */

import type {
  AnnotationItem,
  ErrorCatalogItem,
  EvaluationResults,
  ImageMeta,
  VisionTask,
} from '../types';

let cachedPort: number | null = null;

export async function getBackendPort(): Promise<number> {
  if (cachedPort) return cachedPort;
  if (typeof window !== 'undefined' && window.api?.getBackendPort) {
    try {
      const port = await window.api.getBackendPort();
      if (port) {
        cachedPort = port;
        return port;
      }
    } catch {
      // fallback
    }
  }
  if (typeof window !== 'undefined') {
    const urlParams = new URLSearchParams(window.location.search);
    const portParam = urlParams.get('port');
    if (portParam && !isNaN(Number(portParam))) {
      cachedPort = Number(portParam);
      return cachedPort;
    }
  }
  return 8000;
}

export function setCachedPort(port: number | null): void {
  cachedPort = port;
}

export async function getApiBaseUrl(): Promise<string> {
  const port = await getBackendPort();
  return `http://127.0.0.1:${port}`;
}

export function resolveApiUrl(path: string, port?: number): string {
  if (!path) return '';
  if (path.startsWith('http://') || path.startsWith('https://') || path.startsWith('data:') || path.startsWith('blob:')) {
    return path;
  }
  const effectivePort = port || cachedPort || 8000;
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  return `http://127.0.0.1:${effectivePort}${cleanPath}`;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const base = await getApiBaseUrl();
  const url = `${base}${path.startsWith('/') ? path : `/${path}`}`;
  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  });

  if (!response.ok) {
    let errorDetail = `HTTP ${response.status} ${response.statusText}`;
    try {
      const errData = await response.json();
      if (errData.detail) {
        if (typeof errData.detail === 'object') {
          throw errData.detail;
        }
        errorDetail = errData.detail;
      }
    } catch (e) {
      if (typeof e === 'object' && e !== null && !(e instanceof SyntaxError)) {
        throw e;
      }
    }
    throw new Error(errorDetail);
  }

  return response.json();
}

export const api = {
  health: {
    check: () => request<{ status: string; version: string; device: string; device_name: string }>('/health'),
  },

  project: {
    getCurrent: () => request<any>('/api/project/current'),
    create: (data: { name: string; task: VisionTask; project_dir?: string }) =>
      request<any>('/api/project/create', { method: 'POST', body: JSON.stringify(data) }),
    update: (data: Partial<{ name: string; task: VisionTask; active_preset: string }>) =>
      request<any>('/api/project/update', { method: 'PUT', body: JSON.stringify(data) }),
  },

  dataset: {
    import: (data: { folder_path: string; task: VisionTask; validate_images?: boolean }) =>
      request<{
        status: string;
        total_images: number;
        classes: Record<string, number>;
        split: { train: number; val: number; test?: number };
        corrupted_images?: any[];
      }>('/api/dataset/import', { method: 'POST', body: JSON.stringify(data) }),

    generate: (data: {
      task: VisionTask | 'all';
      num_samples: number;
      output_dir: string;
      modality?: 'pcb' | 'wafer' | 'metal' | 'all';
      split_ratio?: number;
      normal_ratio?: number;
      seed?: number;
    }) =>
      request<{
        status: string;
        count: number;
        classes: string[];
        output_dir: string;
        train_count?: number;
        val_count?: number;
      }>('/api/dataset/generate', { method: 'POST', body: JSON.stringify(data) }),

    split: (data: { folder_path?: string; train_ratio: number; seed?: number }) =>
      request<{ status: string; split: { train: number; val: number } }>('/api/dataset/split', {
        method: 'POST',
        body: JSON.stringify(data),
      }),

    getImages: (params: { folder_path?: string; limit?: number; offset?: number; split?: string; class_name?: string }) => {
      const q = new URLSearchParams();
      if (params.folder_path) q.set('folder_path', params.folder_path);
      if (params.limit !== undefined) q.set('limit', String(params.limit));
      if (params.offset !== undefined) q.set('offset', String(params.offset));
      if (params.split) q.set('split', params.split);
      if (params.class_name) q.set('class_name', params.class_name);
      return request<{ total: number; limit: number; offset: number; items: ImageMeta[] }>(
        `/api/dataset/images?${q.toString()}`
      );
    },
  },

  annotations: {
    get: (imageId: string, dirPath?: string, filePath?: string) => {
      const q = new URLSearchParams();
      if (dirPath) q.set('dir_path', dirPath);
      if (filePath) q.set('file_path', filePath);
      const queryStr = q.toString() ? `?${q.toString()}` : '';
      return request<{
        image_id: string;
        annotations: AnnotationItem[];
        image_width?: number;
        image_height?: number;
        mask_file?: string;
      }>(`/api/annotations/${encodeURIComponent(imageId)}${queryStr}`);
    },
    save: (data: { image_id: string; annotations: AnnotationItem[]; image_width?: number; image_height?: number; output_dir?: string }) =>
      request<{ status: string; count: number; mask_generated: boolean }>('/api/annotations/save', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    autoSelect: (data: { image_path?: string; image_id?: string; seed_x: number; seed_y: number; tolerance?: number }) =>
      request<{
        status: string;
        result: {
          polygon: Array<[number, number]>;
          bbox: [number, number, number, number];
          area: number;
          seed_point: [number, number];
        };
      }>('/api/annotations/auto-select', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    shapeConverter: (data: { image_path?: string; image_id?: string; bbox: [number, number, number, number]; sensitivity?: number }) =>
      request<{
        status: string;
        result: {
          polygon: Array<[number, number]>;
          area: number;
          source_bbox: [number, number, number, number];
        };
      }>('/api/annotations/shape-converter', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  },

  training: {
    start: (data: {
      task: VisionTask;
      preset: 'fast' | 'precision';
      dataset_path: string;
      output_dir?: string;
      config_overrides?: any;
    }) => request<{ job_id: string; status: string; preset: string; task: string }>('/api/training/start', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

    stop: (jobId?: string) =>
      request<{ status: string; job_id: string | null }>('/api/training/stop', {
        method: 'POST',
        body: JSON.stringify({ job_id: jobId }),
      }),

    getStatus: (jobId?: string) => {
      const q = jobId ? `?job_id=${encodeURIComponent(jobId)}` : '';
      return request<any>(`/api/training/status${q}`);
    },
  },

  evaluation: {
    getResults: (jobId?: string, datasetPath?: string, forceRecompute?: boolean) => {
      const q = new URLSearchParams();
      if (jobId) q.set('job_id', jobId);
      if (datasetPath) q.set('dataset_path', datasetPath);
      if (forceRecompute) q.set('force_recompute', 'true');
      return request<EvaluationResults>(`/api/evaluation/results?${q.toString()}`);
    },

    getHeatmap: (imageId: string, jobId?: string, threshold = 0.5, filePath?: string) => {
      const q = new URLSearchParams();
      if (jobId) q.set('job_id', jobId);
      q.set('threshold', String(threshold));
      q.set('format', 'base64');
      if (filePath) q.set('file_path', filePath);
      return request<{
        image_id: string;
        threshold: number;
        confidence_score: number;
        overlay_base64: string;
        predictions: any[];
        latency_ms: number;
      }>(`/api/evaluation/heatmap/${encodeURIComponent(imageId)}?${q.toString()}`);
    },

    getOverkillUnderkill: (params: {
      job_id?: string;
      target_max_underkill?: number;
      cost_escape?: number;
      cost_scrap?: number;
      current_threshold?: number;
    }) => {
      const q = new URLSearchParams();
      if (params.job_id) q.set('job_id', params.job_id);
      if (params.target_max_underkill !== undefined) q.set('target_max_underkill', String(params.target_max_underkill));
      if (params.cost_escape !== undefined) q.set('cost_escape', String(params.cost_escape));
      if (params.cost_scrap !== undefined) q.set('cost_scrap', String(params.cost_scrap));
      if (params.current_threshold !== undefined) q.set('current_threshold', String(params.current_threshold));
      return request<any>(`/api/evaluation/overkill-underkill?${q.toString()}`);
    },

    runBenchmark: (data: { job_id?: string; iterations?: number; resolution?: number }) =>
      request<any>('/api/evaluation/benchmark', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  },

  flowchart: {
    getPipeline: () => request<any>('/api/flowchart/pipeline'),
    savePipeline: (data: any) =>
      request<{ status: string; pipeline_id: string; node_count: number }>('/api/flowchart/pipeline', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    run: (data: { image_path?: string; image_id?: string; pipeline?: any }) =>
      request<any>('/api/flowchart/run', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  },

  export: {
    runtime: (data: {
      job_id?: string;
      export_format?: string;
      resolution?: number;
      package_name?: string;
    }) =>
      request<{
        status: string;
        package_name: string;
        package_path: string;
        manifest: Array<{ name: string; size_kb: number }>;
        total_files: number;
      }>('/api/export/runtime', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  },

  report: {
    export: (data: { job_id?: string; format: 'html' | 'json'; include_images?: boolean; output_path?: string }) =>
      request<{ status: string; file_path: string; format: string; content?: string }>('/api/report/export', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  },

  errors: {
    list: () => request<{ errors: ErrorCatalogItem[] }>('/api/errors'),
    get: (code: string) => request<ErrorCatalogItem>(`/api/errors/${code}`),
  },
};
