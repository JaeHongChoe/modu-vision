const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const root = path.resolve(__dirname, '..');
const originalTs = Module._extensions['.ts'];
const originalTsx = Module._extensions['.tsx'];
const originalRequire = Module.prototype.require;
const datasetState = { isSplitting: false };

function compile(module, filename) {
  const source = require('node:fs').readFileSync(filename, 'utf8');
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
    fileName: filename,
  }).outputText;
  module._compile(output, filename);
}
Module._extensions['.ts'] = compile;
Module._extensions['.tsx'] = compile;
Module.prototype.require = function (specifier) {
  if (specifier === './useDatasetStore' && this.filename.endsWith('useTrainingStore.ts')) {
    return { useDatasetStore: { getState: () => datasetState } };
  }
  return originalRequire.call(this, specifier);
};

const { api } = require(path.join(root, 'src/renderer/services/api.ts'));
const realFetch = global.fetch;
let selected = null;
let profiles = [];
let ready = true;
let requests = [];
let activeJob = null;
let rejectSelection = false;

function response(value, status = 200) {
  return { ok: status < 400, status, statusText: status < 400 ? 'OK' : 'Error',
    json: async () => {
      if (status === 204) throw new SyntaxError('Unexpected end of JSON input');
      return value;
    } };
}

global.fetch = async (url, options = {}) => {
  const endpoint = new URL(url).pathname;
  const method = options.method || 'GET';
  const body = options.body ? JSON.parse(options.body) : undefined;
  requests.push({ endpoint, method, body });
  if (endpoint === '/api/compute/profiles' && method === 'GET') return response({ profiles });
  if (endpoint === '/api/compute/profiles' && method === 'POST') {
    const profile = { ...body, id: body.id || 'server-1' };
    profiles = [...profiles.filter((item) => item.id !== profile.id), profile];
    return response({ profile });
  }
  if (endpoint.startsWith('/api/compute/profiles/') && method === 'DELETE') {
    const id = decodeURIComponent(endpoint.split('/')[4]);
    profiles = profiles.filter((item) => item.id !== id);
    if (selected === id) selected = null;
    return response(undefined, 204);
  }
  if (endpoint.endsWith('/probe') && method === 'POST') {
    return response({ ready, device_name: ready ? 'NVIDIA L40S' : null, device_type: ready ? 'cuda' : null,
      checks: { ssh: ready, runtime: ready }, message: ready ? 'Ready' : 'SSH unavailable' });
  }
  if (endpoint === '/api/compute/selection' && method === 'GET') return response({ compute_profile_id: selected });
  if (endpoint === '/api/compute/selection' && method === 'PUT') {
    if (rejectSelection) return response({ detail: 'Selection write failed' }, 503);
    selected = body.compute_profile_id;
    return response({ compute_profile_id: selected });
  }
  if (endpoint === '/api/training/start' && method === 'POST') {
    activeJob = { job_id: 'job-remote', status: 'preparing', phase: 'preparing',
      compute_profile_id: body.compute_profile_id ?? null };
    return response({ job_id: activeJob.job_id, status: 'started', preset: body.preset,
      task: body.task, compute_profile_id: activeJob.compute_profile_id });
  }
  if (endpoint === '/api/training/status' && method === 'GET') {
    return response(activeJob || { job_id: null, status: 'idle' });
  }
  if (endpoint === '/api/training/reconnect' && method === 'POST') {
    if (!activeJob || body.job_id !== activeJob.job_id) return response({ detail: 'Wrong job' }, 409);
    activeJob = { ...activeJob, status: 'running', phase: 'reconnecting' };
    return response({ job_id: activeJob.job_id, status: activeJob.status,
      compute_profile_id: activeJob.compute_profile_id });
  }
  if (endpoint === '/api/training/stop' && method === 'POST') return response({ status: 'stopping', job_id: body.job_id });
  throw new Error(`Unexpected ${method} ${endpoint}`);
};

const server = {
  id: 'server-1', name: 'Server 42', ssh_target: 'gpu.example.test', ssh_port: 22,
  remote_root: '/data/home/kai/modu-vision', runtime_kind: 'python',
  runtime_value: '/opt/modu-vision/bin/python', gpu_selector: '0',
};

test('training cannot start before the saved compute selection is loaded', async () => {
  const { useTrainingStore } = require(path.join(root, 'src/renderer/stores/useTrainingStore.ts'));
  await assert.rejects(useTrainingStore.getState().startTraining('/local/data', 'segmentation'), /컴퓨팅 위치/);
  assert.equal(requests.some((item) => item.endpoint === '/api/training/start'), false);
});

test('profile selection persists and failed selection does not silently choose local', async () => {
  const { useComputeStore } = require(path.join(root, 'src/renderer/stores/useComputeStore.ts'));
  await useComputeStore.getState().load();
  await useComputeStore.getState().saveProfile(server);
  await useComputeStore.getState().selectTarget(server.id);
  assert.equal(useComputeStore.getState().selectedProfileId, server.id);
  assert.equal(selected, server.id);
  rejectSelection = true;
  await assert.rejects(useComputeStore.getState().selectTarget(null), /Selection write failed/);
  assert.equal(useComputeStore.getState().selectedProfileId, server.id);
  rejectSelection = false;
});

test('remote training requires a ready probe and sends its profile ID', async () => {
  const { useComputeStore } = require(path.join(root, 'src/renderer/stores/useComputeStore.ts'));
  const { useTrainingStore } = require(path.join(root, 'src/renderer/stores/useTrainingStore.ts'));
  requests = [];
  ready = false;
  await useComputeStore.getState().probeProfile(server.id);
  await assert.rejects(useTrainingStore.getState().startTraining('/local/data', 'segmentation'), /연결 검사|준비/);
  assert.equal(requests.some((item) => item.endpoint === '/api/training/start'), false);
  ready = true;
  await useComputeStore.getState().probeProfile(server.id);
  await useTrainingStore.getState().startTraining('/local/data', 'segmentation');
  const start = requests.find((item) => item.endpoint === '/api/training/start');
  assert.equal(start.body.compute_profile_id, server.id);
  assert.equal(useTrainingStore.getState().jobComputeProfileId, server.id);
  assert.equal(useTrainingStore.getState().status, 'preparing');
});

test('switching selection keeps running job bound to its original server; disconnect can reconnect', async () => {
  const { useComputeStore } = require(path.join(root, 'src/renderer/stores/useComputeStore.ts'));
  const { useTrainingStore } = require(path.join(root, 'src/renderer/stores/useTrainingStore.ts'));
  await useComputeStore.getState().selectTarget(null);
  assert.equal(useTrainingStore.getState().jobComputeProfileId, server.id);
  activeJob = { ...activeJob, status: 'disconnected', phase: 'disconnected' };
  await useTrainingStore.getState().refreshCurrentJob();
  assert.equal(useTrainingStore.getState().status, 'disconnected');
  assert.equal(useTrainingStore.getState().isTraining, true);
  requests = [];
  await useTrainingStore.getState().reconnectCurrentJob();
  assert.equal(requests.find((item) => item.endpoint === '/api/training/reconnect').body.job_id, 'job-remote');
  assert.equal(useTrainingStore.getState().jobComputeProfileId, server.id);
  activeJob = { ...activeJob, status: 'running', phase: 'running', current_epoch: 2 };
  await useTrainingStore.getState().refreshCurrentJob();
  assert.equal(useTrainingStore.getState().status, 'running');
  assert.equal(useTrainingStore.getState().currentEpoch, 2);
  assert.equal(useTrainingStore.getState().jobComputeProfileId, server.id);
  activeJob = { ...activeJob, compute_profile_id: 'other-server', current_epoch: 99 };
  await useTrainingStore.getState().refreshCurrentJob();
  assert.equal(useTrainingStore.getState().jobComputeProfileId, server.id);
  assert.equal(useTrainingStore.getState().currentEpoch, 2);
  assert.equal(useTrainingStore.getState().status, 'disconnected');
  assert.match(useTrainingStore.getState().jobStatusError, /서버|위치/);
  activeJob = { ...activeJob, compute_profile_id: server.id, status: 'completed', phase: 'syncing' };
  await useTrainingStore.getState().refreshCurrentJob();
  assert.equal(useTrainingStore.getState().status, 'completed');
  assert.equal(useTrainingStore.getState().jobPhase, 'completed');
});

test('local training omits compute_profile_id', async () => {
  const { useTrainingStore } = require(path.join(root, 'src/renderer/stores/useTrainingStore.ts'));
  requests = [];
  await useTrainingStore.getState().startTraining('/local/data', 'segmentation');
  const start = requests.find((item) => item.endpoint === '/api/training/start');
  assert.equal(Object.hasOwn(start.body, 'compute_profile_id'), false);
  assert.equal(useTrainingStore.getState().jobComputeProfileId, null);
});

test('server management panel shows selected target and probe readiness', () => {
  const React = require('react');
  const { renderToStaticMarkup } = require('react-dom/server');
  const { useComputeStore } = require(path.join(root, 'src/renderer/stores/useComputeStore.ts'));
  useComputeStore.setState({ selectedProfileId: server.id, probeResults: {
    [server.id]: { ready: true, device_name: 'NVIDIA L40S', checks: { ssh: true } },
  } });
  const liveRequire = Module.prototype.require;
  Module.prototype.require = function (specifier) {
    if (specifier === '../../stores/useComputeStore' && this.filename.endsWith('ComputeServerPanel.tsx')) {
      return { useComputeStore: () => useComputeStore.getState() };
    }
    return liveRequire.call(this, specifier);
  };
  const { ComputeServerPanel } = require(path.join(root, 'src/renderer/components/compute/ComputeServerPanel.tsx'));
  Module.prototype.require = liveRequire;
  const html = renderToStaticMarkup(React.createElement(ComputeServerPanel, { onClose: () => {} }));
  assert.match(html, /Server 42/);
  assert.match(html, /NVIDIA L40S/);
  assert.match(html, /연결 검사/);
  assert.match(html, /서버 추가/);
});

test('header exposes current compute target and local option', () => {
  const React = require('react');
  const { renderToStaticMarkup } = require('react-dom/server');
  const { useComputeStore } = require(path.join(root, 'src/renderer/stores/useComputeStore.ts'));
  const { useTrainingStore } = require(path.join(root, 'src/renderer/stores/useTrainingStore.ts'));
  useTrainingStore.setState({ jobId: 'job-remote', jobComputeProfileId: server.id,
    jobComputeLabel: server.name, status: 'completed', isTraining: false });
  const liveRequire = Module.prototype.require;
  Module.prototype.require = function (specifier) {
    if (this.filename.endsWith('WizardHeader.tsx') && specifier === '../../stores/useComputeStore') {
      return { useComputeStore: () => useComputeStore.getState() };
    }
    if (this.filename.endsWith('WizardHeader.tsx') && specifier === '../../stores/useProjectStore') {
      return { useProjectStore: () => ({ activeStep: 4, setStep() {}, task: 'segmentation', setTask() {},
        language: 'ko', setLanguage() {}, backendStatus: { healthy: true, port: 8000, device: 'mps' },
        projectName: 'Test project' }) };
    }
    if (this.filename.endsWith('WizardHeader.tsx') && specifier === '../../stores/useTrainingStore') {
      return { useTrainingStore: () => useTrainingStore.getState() };
    }
    return liveRequire.call(this, specifier);
  };
  const { WizardHeader } = require(path.join(root, 'src/renderer/components/wizard/WizardHeader.tsx'));
  Module.prototype.require = liveRequire;
  useComputeStore.setState({ selectedProfileId: server.id });
  const html = renderToStaticMarkup(React.createElement(WizardHeader));
  assert.match(html, /Compute:/);
  assert.match(html, /This computer/);
  assert.match(html, /Server 42/);
  assert.match(html, /서버 관리/);
  assert.match(html, /현재 모델 위치:.*Server 42/);
});

test('Stage 3 shows the job-bound server and reconnect state after selection changes', () => {
  const React = require('react');
  const { renderToStaticMarkup } = require('react-dom/server');
  const { useTrainingStore } = require(path.join(root, 'src/renderer/stores/useTrainingStore.ts'));
  const { useComputeStore } = require(path.join(root, 'src/renderer/stores/useComputeStore.ts'));
  useComputeStore.setState({ selectedProfileId: null });
  useTrainingStore.setState({ status: 'disconnected', isTraining: true, jobId: 'job-remote',
    jobComputeProfileId: server.id, jobComputeLabel: server.id, jobDeviceName: 'NVIDIA L40S',
    jobPhase: 'disconnected', jobStatusError: 'SSH unavailable' });
  const liveRequire = Module.prototype.require;
  Module.prototype.require = function (specifier) {
    if (this.filename.endsWith('TrainingController.tsx')) {
      if (specifier === '../../stores/useProjectStore') return { useProjectStore: () => ({ task: 'segmentation', language: 'ko', setStep() {} }) };
      if (specifier === '../../stores/useDatasetStore') return { useDatasetStore: () => ({ folderPath: '/local/data', totalImages: 10, split: { train: 8, val: 2 },
        isLoading: false, isSplitting: false, importError: null, splitError: null, splitSupported: true, splitUnavailableReason: null, applySplit() {} }) };
      if (specifier === '../../stores/useTrainingStore') return { useTrainingStore: () => useTrainingStore.getState() };
      if (specifier === '../../stores/useComputeStore') return { useComputeStore: () => useComputeStore.getState() };
      if (specifier.startsWith('../common/') || specifier.startsWith('./')) {
        return new Proxy({}, { get: () => () => null });
      }
    }
    return liveRequire.call(this, specifier);
  };
  const { TrainingController } = require(path.join(root, 'src/renderer/components/training/TrainingController.tsx'));
  Module.prototype.require = liveRequire;
  const html = renderToStaticMarkup(React.createElement(TrainingController));
  assert.match(html, /Server 42/);
  assert.match(html, /NVIDIA L40S/);
  assert.match(html, /원격 장치/);
  assert.match(html, /연결 끊김/);
  assert.match(html, /연결 다시 확인/);

  useComputeStore.setState({ selectedProfileId: server.id });
  useTrainingStore.setState({ status: 'idle', isTraining: false, jobId: null,
    jobComputeProfileId: null, jobComputeLabel: null, jobDeviceName: null });
  const idleHtml = renderToStaticMarkup(React.createElement(TrainingController));
  assert.match(idleHtml, /선택한 원격 장치/);
  assert.match(idleHtml, /Server 42/);
  assert.doesNotMatch(idleHtml, /HARDWARE TELEMETRY/);
});

test('deleting a saved profile handles the empty HTTP 204 response', async () => {
  const { useComputeStore } = require(path.join(root, 'src/renderer/stores/useComputeStore.ts'));
  await useComputeStore.getState().selectTarget(server.id);
  await useComputeStore.getState().deleteProfile(server.id);
  assert.equal(useComputeStore.getState().selectedProfileId, null);
  assert.equal(useComputeStore.getState().profiles.some((profile) => profile.id === server.id), false);
});

test.after(() => {
  global.fetch = realFetch;
  Module.prototype.require = originalRequire;
  Module._extensions['.ts'] = originalTs;
  Module._extensions['.tsx'] = originalTsx;
});
