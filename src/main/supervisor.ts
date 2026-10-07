/**
 * src/main/supervisor.ts
 *
 * Native Child Process Lifecycle Supervisor for Vision AI Studio.
 * Features:
 *   - Auto-resolves Python executable across Conda, virtualenvs, bundled resources, and system PATH
 *   - Spawns backend/main.py with ephemeral port 0 and unbuffered I/O
 *   - Chunk-split-proof line buffering regex discovery for VISION_AI_STUDIO_PORT=<port>
 *   - Exponential backoff HTTP health check polling against GET /health (15s timeout)
 *   - Graceful shutdown (stdin closed on every platform, SIGTERM on POSIX) with a 4.0s timeout, then a force stop of the
 *     backend process only (owned training workers live in their own session and are reattached on restart)
 *   - Crash detection, circular log buffer, and auto-restart resilience (max 3 retries / 60s)
 *   - IPC port and status provider conforming to PROJECT.md contracts
 */

import { ChildProcess, execSync, spawn } from 'child_process';
import { createHash, randomBytes } from 'crypto';
import { EventEmitter } from 'events';
import fs from 'fs';
import http from 'http';
import os from 'os';
import path from 'path';
import {OwnedApplicationLaunch} from './applicationLaunch';

function getElectronApp(): any {
  try {
    const electron = require('electron');
    return electron && typeof electron === 'object' && electron.app ? electron.app : null;
  } catch {
    return null;
  }
}

export type ProcessState = 'STOPPED' | 'STARTING' | 'HEALTHY' | 'STOPPING' | 'CRASHED';

export interface BackendHealth {
  status: 'ok' | string;
  version: string;
  device: 'mps' | 'cuda' | 'cpu';
  device_name: string;
  is_accelerated?: boolean;
  torch_version?: string;
}

export interface BackendStatus {
  port: number | null;
  healthy: boolean;
  pid: number | null;
  device?: string;
  deviceName?: string;
}

export interface BackendStatusInfo extends BackendStatus {
  state: ProcessState;
  pythonPath: string | null;
  health: BackendHealth | null;
  restartCount: number;
  uptimeSeconds: number | null;
  runtimeIdentity: {mode:'frozen';build_identity_sha256:string;executable_sha256:string}|null;
}

export interface CrashEventData {
  exitCode: number | null;
  signal: NodeJS.Signals | string | null;
  recentStderr: string[];
  recentStdout: string[];
  restartsAttempted: number;
  message: string;
}

export interface SupervisorConfig {
  pythonPath?: string;
  backendScriptPath?: string;
  projectDir?: string;
  host?: string;
  logLevel?: 'debug' | 'info' | 'warning' | 'error';
  portDiscoveryTimeoutMs?: number;
  healthCheckTimeoutMs?: number;
  gracefulShutdownTimeoutMs?: number;
  autoRestart?: boolean;
  maxRestartAttempts?: number;
  restartCooldownWindowMs?: number;
  ownedApplicationLaunch?: OwnedApplicationLaunch;
}

const MAX_LOG_BUFFER_LINES = 100;

/** Spawn options for the backend. Its stdin stays open as the graceful stop channel, closed by stopBackend (when the
 *  app itself exits, the exit hook force-stops the backend instead), and no console window opens for it on Windows. */
export function backendSpawnOptions(cwd: string, env: NodeJS.ProcessEnv, owned=false) {
  return {
    cwd,
    env: { ...env, VISION_AI_STUDIO_STOP_ON_STDIN_EOF: '1' },
    stdio: owned ? ['pipe', 'pipe', 'pipe', 'pipe'] as ['pipe','pipe','pipe','pipe'] : ['pipe', 'pipe', 'pipe'] as ['pipe', 'pipe', 'pipe'],
    windowsHide: true,
  };
}

/** Force-stop the backend process itself through the handle Node holds for it, never by process number: once Node has
 *  seen the backend exit the handle is gone and this does nothing, and Windows does not reuse a process number while the
 *  handle is open (there it terminates that one process). Only the backend is stopped, never its process tree: owned
 *  training workers run in their own session (POSIX) or process group (Windows), and they, managed services and the
 *  operations watcher are reattached by identity by the next backend; other helpers end on their own deadlines. */
export function forceStop(proc: ChildProcess): void {
  try {
    proc.kill('SIGKILL');
  } catch {
    // Process already dead
  }
}

export class BackendSupervisor extends EventEmitter {
  private runtimeIdentity:BackendStatusInfo['runtimeIdentity']=null;
  private config: Required<Omit<SupervisorConfig,'ownedApplicationLaunch'>>;
  private ownedApplicationLaunch:OwnedApplicationLaunch|undefined;
  private ownedBackendAttempted=false;
  private state: ProcessState = 'STOPPED';
  private childProcess: ChildProcess | null = null;
  private port: number | null = null;
  private apiToken: string | null = null;
  private healthInfo: BackendHealth | null = null;
  private resolvedPythonPath: string | null = null;
  private startTime: number | null = null;

  private isShuttingDown = false;
  private restartCount = 0;
  private restartTimestamps: number[] = [];

  private stdoutBuffer: string[] = [];
  private stderrBuffer: string[] = [];

  constructor(customConfig?: SupervisorConfig) {
    super();
    this.ownedApplicationLaunch=customConfig?.ownedApplicationLaunch;

    // Default configuration
    this.config = {
      pythonPath: customConfig?.pythonPath || '',
      backendScriptPath: customConfig?.backendScriptPath || '',
      projectDir: customConfig?.projectDir || '',
      host: customConfig?.host || '127.0.0.1',
      logLevel: customConfig?.logLevel || 'info',
      portDiscoveryTimeoutMs: customConfig?.portDiscoveryTimeoutMs || 15000,
      healthCheckTimeoutMs: customConfig?.healthCheckTimeoutMs || 15000,
      gracefulShutdownTimeoutMs: customConfig?.gracefulShutdownTimeoutMs || 4000,
      autoRestart: customConfig?.autoRestart ?? true,
      maxRestartAttempts: customConfig?.maxRestartAttempts || 3,
      restartCooldownWindowMs: customConfig?.restartCooldownWindowMs || 60000,
    };

    // Process-level safety net for emergency termination
    this.registerProcessHooks();
  }

  // --------------------------------------------------------------------------
  // Public Accessors
  // --------------------------------------------------------------------------

  public getPort(): number | null {
    return this.port;
  }

  public getApiToken(): string | null {
    return this.apiToken;
  }

  public isHealthy(): boolean {
    return this.state === 'HEALTHY' && this.port !== null;
  }

  public getPid(): number | null {
    return this.childProcess?.pid ?? null;
  }

  public isRunning(): boolean {
    return this.state === 'STARTING' || this.state === 'HEALTHY';
  }

  public getStatus(): BackendStatus {
    return {
      port: this.port,
      healthy: this.isHealthy(),
      pid: this.getPid(),
      device: this.healthInfo?.device,
      deviceName: this.healthInfo?.device_name,
    };
  }

  public getStatusInfo(): BackendStatusInfo {
    const uptime = this.startTime && this.state === 'HEALTHY'
      ? Math.floor((Date.now() - this.startTime) / 1000)
      : null;

    return {
      ...this.getStatus(),
      state: this.state,
      pythonPath: this.resolvedPythonPath,
      health: this.healthInfo,
      restartCount: this.restartCount,
      uptimeSeconds: uptime,
      runtimeIdentity:this.runtimeIdentity,
    };
  }

  // --------------------------------------------------------------------------
  // Lifecycle Methods (Dual Method Signatures for Peer Compatibility)
  // --------------------------------------------------------------------------

  public async start(): Promise<number> {
    return this.startBackend();
  }

  public async stop(): Promise<void> {
    return this.stopBackend();
  }

  public async startBackend(): Promise<number> {
    if (this.state === 'HEALTHY' && this.port !== null) {
      return this.port;
    }

    if (this.state === 'STARTING') {
      return new Promise((resolve, reject) => {
        this.once('ready', (port: number) => resolve(port));
        this.once('crashed', (err) => reject(new Error(err.message)));
      });
    }

    this.isShuttingDown = false;
    this.setState('STARTING');
    const apiToken = randomBytes(32).toString('hex');
    this.apiToken = apiToken;

    try {
      const owned=this.ownedApplicationLaunch;
      if(owned&&this.ownedBackendAttempted)throw new Error('Owned backend restart requires reconciled launch ownership');
      const projectDir = owned?.projects || this.resolveProjectDir();
      const standaloneBin = this.resolveStandaloneBinary();
      let spawnBin: string;
      let spawnArgs: string[];

      if (standaloneBin) {
        if(owned){const artifact=owned.executable(standaloneBin);if(!artifact.build)throw new Error('Owned native backend requires its committed frozen inventory');this.runtimeIdentity={mode:'frozen',build_identity_sha256:artifact.build,executable_sha256:artifact.sha256};}
        this.resolvedPythonPath = standaloneBin;
        spawnBin = standaloneBin;
        spawnArgs = [
          '--host', this.config.host,
          '--port', '0',
          '--project-dir', projectDir,
          '--log-level', this.config.logLevel,
        ];
        console.log(`[Supervisor] Launching compiled standalone backend: ${spawnBin} ${spawnArgs.join(' ')}`);
      } else {
        if(owned)throw new Error('Owned native launch requires its committed frozen backend');
        const pythonBin = this.resolvePython();
        this.resolvedPythonPath = pythonBin;

        // Auto-validate and self-heal missing Python libraries
        this.ensureDependencies(pythonBin);

        const scriptPath = this.resolveBackendScript();
        spawnBin = pythonBin;
        spawnArgs = [
          scriptPath,
          '--host', this.config.host,
          '--port', '0', // Request OS ephemeral port
          '--project-dir', projectDir,
          '--log-level', this.config.logLevel,
        ];
        console.log(`[Supervisor] Launching Python backend: ${spawnBin} ${spawnArgs.join(' ')}`);
      }
      if(owned)spawnArgs.push('--shared-auth-dir',owned.auth);

      const appRoot = this.getAppRoot();
      const backendCwd = this.getBackendWorkingDirectory(appRoot);
      const userDataDir = getElectronApp()?.getPath('userData');
      let env:NodeJS.ProcessEnv = {
        ...process.env,
        PYTHONUNBUFFERED: '1',
        PYTHONDONTWRITEBYTECODE: '1',
        PYTHONPATH: appRoot,
        VISION_AI_STUDIO_API_TOKEN: apiToken,
        VISION_AI_APP_VERSION: getElectronApp()?.getVersion() || '',
        ...(userDataDir ? { VISION_AI_STUDIO_USER_DATA_DIR: userDataDir } : {}),
      };
      if(owned)env=owned.backendEnvironment(env);
      else if(Object.keys(env).some(key=>key.startsWith('VISION_APPLICATION_')))throw new Error('Unauthenticated application launch context cannot spawn a backend');

      try {
        if(owned)this.ownedBackendAttempted=true;
        this.childProcess = spawn(spawnBin, spawnArgs, backendSpawnOptions(backendCwd, env,!!owned));
      } catch (spawnError: any) {
        this.setState('CRASHED');
        throw new Error(`Failed to spawn backend process at '${spawnBin}': ${spawnError.message}`);
      }

      const pid = this.childProcess.pid;
      console.log(`[Supervisor] Python daemon spawned with PID ${pid}`);
      // Closing the stop channel of a backend that already exited reports EPIPE; it must not crash the main process.
      this.childProcess.stdin?.on('error', () => {});

      // Attach stream listeners
      this.attachProcessListeners(this.childProcess);
      const bootstrap=owned?.bindBackend(this.childProcess,spawnBin,this.runtimeIdentity?.build_identity_sha256||null);
      // Attach immediately so an early descriptor refusal is always handled.
      bootstrap?.catch(()=>{});

      // 1. Discover Ephemeral Port from stdout
      // Frozen model libraries need time for first-launch OS verification and
      // initialization. A packaged app must not fall back to system Python.
      const startupTimeout=standaloneBin&&getElectronApp()?.isPackaged
        ? Math.max(this.config.portDiscoveryTimeoutMs,180000):this.config.portDiscoveryTimeoutMs;
      const port = await this.discoverPort(this.childProcess, startupTimeout);
      this.port = port;
      console.log(`[Supervisor] Ephemeral port discovered: ${port}`);

      // Owned recovery and binding must finish before the HTTP health budget.
      if(bootstrap)await bootstrap;
      // 2. Poll /health endpoint with exponential backoff
      const health = await this.pollHealth(
        port,
        this.config.host,
        this.config.healthCheckTimeoutMs
      );
      this.healthInfo = health;
      this.startTime = Date.now();
      this.setState('HEALTHY');

      console.log(`[Supervisor] Backend healthy on port ${port} (Device: ${health.device}, ${health.device_name})`);
      this.emit('ready', port, health);
      return port;
    } catch (startupError: any) {
      this.ownedApplicationLaunch?.refuse();
      console.error(`[Supervisor] Startup failed: ${startupError.message}`);
      const alreadyReported = this.state === 'CRASHED';
      await this.stopBackend();
      this.setState('CRASHED');
      if (!alreadyReported) this.emit('crashed', {
        exitCode: null, signal: null, recentStderr: [...this.stderrBuffer],
        recentStdout: [...this.stdoutBuffer], restartsAttempted: this.restartCount,
        message: startupError.message,
      } satisfies CrashEventData);
      throw startupError;
    }
  }

  public async stopBackend(): Promise<void> {
    if (this.state === 'STOPPED' && !this.childProcess) {
      return;
    }

    this.isShuttingDown = true;
    this.setState('STOPPING');

    const proc = this.childProcess;
    if (!proc || !proc.pid || proc.killed || proc.exitCode !== null || proc.signalCode !== null) {
      this.cleanupState();
      return;
    }

    const pid = proc.pid;
    console.log(`[Supervisor] Initiating graceful shutdown for PID ${pid}...`);

    await new Promise<void>((resolve) => {
      let forceKillTimer: NodeJS.Timeout | null = null;

      const onExit = (code: number | null, signal: string | null) => {
        if (forceKillTimer) clearTimeout(forceKillTimer);
        console.log(`[Supervisor] Daemon PID ${pid} exited cleanly (code: ${code}, signal: ${signal})`);
        resolve();
      };

      proc.once('exit', onExit);

      // 1. Graceful FastAPI / Uvicorn shutdown: closing stdin asks for it on every platform. POSIX also gets SIGTERM;
      //    on Windows a signal from Node terminates the process outright, which would skip the shutdown sequence.
      try {
        proc.stdin?.end();
      } catch (e) {
        // The pipe may already be closed
      }
      if (process.platform !== 'win32') {
        try {
          proc.kill('SIGTERM');
        } catch (e) {
          // Process might already be terminating
        }
      }

      // 2. 4.0-second timeout fallback -> force stop of the backend process only
      forceKillTimer = setTimeout(() => {
        console.warn(`[Supervisor] Daemon PID ${pid} did not exit within ${this.config.gracefulShutdownTimeoutMs}ms. Forcing it to stop.`);
        proc.removeListener('exit', onExit);
        forceStop(proc);
        resolve();
      }, this.config.gracefulShutdownTimeoutMs);
    });

    this.cleanupState();
  }

  public async restart(): Promise<number> {
    console.log('[Supervisor] Manual restart requested');
    await this.stopBackend();
    return this.startBackend();
  }

  // --------------------------------------------------------------------------
  // Stream & Process Monitoring
  // --------------------------------------------------------------------------

  private attachProcessListeners(proc: ChildProcess): void {
    let stdoutRemainder = '';
    proc.stdout?.on('data', (chunk: Buffer) => {
      stdoutRemainder += chunk.toString();
      let newlineIdx: number;
      while ((newlineIdx = stdoutRemainder.indexOf('\n')) !== -1) {
        const line = stdoutRemainder.slice(0, newlineIdx).replace(/\r$/, '');
        stdoutRemainder = stdoutRemainder.slice(newlineIdx + 1);
        this.appendLog(this.stdoutBuffer, line);
        this.emit('stdout', line);
      }
    });

    let stderrRemainder = '';
    proc.stderr?.on('data', (chunk: Buffer) => {
      stderrRemainder += chunk.toString();
      let newlineIdx: number;
      while ((newlineIdx = stderrRemainder.indexOf('\n')) !== -1) {
        const line = stderrRemainder.slice(0, newlineIdx).replace(/\r$/, '');
        stderrRemainder = stderrRemainder.slice(newlineIdx + 1);
        this.appendLog(this.stderrBuffer, line);
        this.emit('stderr', line);
      }
    });

    proc.on('exit', (code, signal) => {
      console.log(`[Supervisor] Python process exited with code ${code}, signal ${signal}`);
      // A backend force-stopped by an earlier stop may exit after a restart started its successor: its exit is not a
      // crash of the backend now tracked.
      if (this.isShuttingDown || this.childProcess !== proc) {
        return;
      }

      this.handleUnexpectedCrash(code, signal);
    });

    proc.on('error', (err) => {
      console.error(`[Supervisor] Process error:`, err);
    });
  }

  private handleUnexpectedCrash(code: number | null, signal: NodeJS.Signals | string | null): void {
    const crashData: CrashEventData = {
      exitCode: code,
      signal,
      recentStderr: [...this.stderrBuffer],
      recentStdout: [...this.stdoutBuffer],
      restartsAttempted: this.restartCount,
      message: `Python backend process terminated unexpectedly (code: ${code}, signal: ${signal}).`,
    };

    this.childProcess = null;
    this.port = null;
    this.apiToken = null;
    this.healthInfo = null;

    const now = Date.now();
    this.restartTimestamps = this.restartTimestamps.filter(
      (t) => now - t < this.config.restartCooldownWindowMs
    );

    if(this.ownedApplicationLaunch)this.ownedApplicationLaunch.refuse();
    if (!this.ownedApplicationLaunch && this.config.autoRestart && this.restartTimestamps.length < this.config.maxRestartAttempts) {
      this.restartTimestamps.push(now);
      this.restartCount++;
      console.warn(
        `[Supervisor] Unexpected crash. Auto-restart attempt ${this.restartCount}/${this.config.maxRestartAttempts} in 1000ms...`
      );

      setTimeout(() => {
        this.startBackend().catch((err) => {
          console.error('[Supervisor] Auto-restart attempt failed:', err);
        });
      }, 1000);
    } else {
      this.setState('CRASHED');
      console.error('[Supervisor] Max restart attempts exceeded or autoRestart disabled. Emitting crash event.');
      this.emit('crashed', crashData);
    }
  }

  // --------------------------------------------------------------------------
  // Port Discovery & Health Polling
  // --------------------------------------------------------------------------

  private discoverPort(proc: ChildProcess, timeoutMs: number): Promise<number> {
    return new Promise((resolve, reject) => {
      let isResolved = false;
      const portRegex = /VISION_AI_STUDIO_PORT=(\d+)/;

      const timer = setTimeout(() => {
        if (!isResolved) {
          isResolved = true;
          cleanup();
          reject(new Error(`Port discovery timed out after ${timeoutMs}ms. Recent stderr: ${this.stderrBuffer.slice(-5).join(' | ')}`));
        }
      }, timeoutMs);

      const onLine = (line: string) => {
        const match = line.match(portRegex);
        if (match && !isResolved) {
          isResolved = true;
          cleanup();
          resolve(parseInt(match[1], 10));
        }
      };

      const onExit = (code: number | null) => {
        if (!isResolved) {
          isResolved = true;
          cleanup();
          reject(new Error(`Python process exited prematurely with code ${code} before port discovery.`));
        }
      };

      const onError = (error: Error) => {
        if (!isResolved) {
          isResolved = true;
          cleanup();
          reject(error);
        }
      };

      const cleanup = () => {
        clearTimeout(timer);
        this.removeListener('stdout', onLine);
        proc.removeListener('exit', onExit);
        proc.removeListener('error', onError);
      };

      this.on('stdout', onLine);
      proc.once('exit', onExit);
      proc.once('error', onError);
    });
  }

  private async pollHealth(
    port: number,
    host: string,
    timeoutMs: number
  ): Promise<BackendHealth> {
    const startTime = Date.now();
    let currentDelay = 100;
    const maxDelay = 500;

    while (Date.now() - startTime < timeoutMs) {
      if (this.isShuttingDown) {
        throw new Error('Health check aborted due to shutdown.');
      }

      try {
        const health = await this.checkHealthOnce(port, host, 1000);
        if (health && health.status === 'ok') {
          return health;
        }
      } catch (err) {
        // Connection refused or warm-up in progress; retry
      }

      await new Promise((r) => setTimeout(r, currentDelay));
      currentDelay = Math.min(Math.floor(currentDelay * 1.5), maxDelay);
    }

    throw new Error(`Health check timed out after ${timeoutMs}ms on http://${host}:${port}/health`);
  }

  private checkHealthOnce(port: number, host: string, reqTimeoutMs: number): Promise<BackendHealth> {
    return new Promise((resolve, reject) => {
      const req = http.get(
        { host, port, path: '/health', timeout: reqTimeoutMs },
        (res) => {
          if (res.statusCode !== 200) {
            res.resume();
            return reject(new Error(`HTTP ${res.statusCode}`));
          }
          let data = '';
          res.setEncoding('utf8');
          res.on('data', (chunk) => { data += chunk; });
          res.on('end', () => {
            try {
              resolve(JSON.parse(data));
            } catch (err) {
              reject(err);
            }
          });
        }
      );

      req.on('timeout', () => {
        req.destroy();
        reject(new Error('Health check request timed out'));
      });

      req.on('error', (err) => {
        reject(err);
      });
    });
  }

  // --------------------------------------------------------------------------
  // Path Resolution Helpers
  // --------------------------------------------------------------------------

  private resolvePython(): string {
    // 1. Explicit config
    if (this.config.pythonPath && fs.existsSync(this.config.pythonPath)) {
      return this.config.pythonPath;
    }

    // 2. Environment variable
    const envPy = process.env.VISION_AI_PYTHON || process.env.PYTHON_PATH;
    if (envPy && fs.existsSync(envPy)) {
      return envPy;
    }

    // 3. Bundled Python runtime inside packaged app
    const app = getElectronApp();
    if (app && app.isPackaged) {
      const bundledPy = process.platform === 'win32'
        ? path.join(process.resourcesPath, 'python', 'python.exe')
        : path.join(process.resourcesPath, 'python', 'bin', 'python3');
      if (fs.existsSync(bundledPy)) {
        return bundledPy;
      }
    }

    // 4. Local virtual environments (.venv, venv)
    const appRoot = this.getAppRoot();
    const venvCandidates = process.platform === 'win32'
      ? [
          path.join(appRoot, '.venv', 'Scripts', 'python.exe'),
          path.join(appRoot, 'venv', 'Scripts', 'python.exe'),
        ]
      : [
          path.join(appRoot, '.venv', 'bin', 'python3'),
          path.join(appRoot, '.venv', 'bin', 'python'),
          path.join(appRoot, 'venv', 'bin', 'python3'),
          path.join(appRoot, 'venv', 'bin', 'python'),
        ];

    for (const cand of venvCandidates) {
      if (fs.existsSync(cand)) return cand;
    }

    // 5. Common Conda / Homebrew paths on macOS/Linux/Windows
    const homedir = os.homedir();
    const knownCandidates = process.platform === 'win32'
      ? [
          path.join(process.env.USERPROFILE || '', 'anaconda3', 'python.exe'),
          path.join(process.env.USERPROFILE || '', 'miniconda3', 'python.exe'),
          'C:\\ProgramData\\anaconda3\\python.exe',
          'C:\\ProgramData\\miniconda3\\python.exe',
        ]
      : [
          '/opt/anaconda3/bin/python3',
          '/opt/homebrew/bin/python3',
          '/usr/local/bin/python3',
          path.join(homedir, 'anaconda3', 'bin', 'python3'),
          path.join(homedir, 'miniconda3', 'bin', 'python3'),
          path.join(homedir, '.conda', 'envs', 'vision_ai', 'bin', 'python3'),
        ];

    for (const cand of knownCandidates) {
      if (fs.existsSync(cand)) return cand;
    }

    // 6. System PATH fallback
    return process.platform === 'win32' ? 'python' : 'python3';
  }

  private resolveStandaloneBinary(): string | null {
    const binName = process.platform === 'win32' ? 'vision_ai_backend.exe' : 'vision_ai_backend';
    const app = getElectronApp();
    const appRoot = this.getAppRoot();

    // 1. Packaged resources path
    if (app && app.isPackaged) {
      const candidates = [
        path.join(process.resourcesPath, 'bin', binName),
        path.join(process.resourcesPath, 'backend_bin', binName),
        path.join(process.resourcesPath, binName),
      ];
      for (const cand of candidates) {
        if (fs.existsSync(cand) && fs.statSync(cand).isFile()) {
          const receiptPath=path.join(path.dirname(cand),'backend-release.json');
          if(!fs.existsSync(receiptPath))throw new Error('Packaged frozen backend inventory is missing; rebuild the backend on this platform');
          const receipt=JSON.parse(fs.readFileSync(receiptPath,'utf8'));
          const digest=createHash('sha256').update(fs.readFileSync(cand)).digest('hex');
          if(receipt.executable!==binName||receipt.executable_sha256!==digest||!receipt.inventory?.build_identity_sha256)throw new Error('Packaged frozen backend checksum or build identity differs from its inventory');
          this.runtimeIdentity={mode:'frozen',build_identity_sha256:receipt.inventory.build_identity_sha256,executable_sha256:digest};
          return cand;
        }
      }
      throw new Error('Packaged frozen backend is missing. Build it on the target OS and architecture before packaging.');
    }

    if (process.env.VISION_AI_STUDIO_DEV_SOURCE_BACKEND === '1') return null;

    // 2. Local dist-backend build folder
    const distBin = path.join(appRoot, 'dist-backend', binName);
    if (fs.existsSync(distBin) && fs.statSync(distBin).isFile()) return distBin;
    const directoryBin=path.join(appRoot,'dist-backend','vision_ai_backend',binName);
    if(fs.existsSync(directoryBin) && fs.statSync(directoryBin).isFile())return directoryBin;

    return null;
  }

  private ensureDependencies(pythonBin: string): void {
    try {
      const checkCode = 'import fastapi, uvicorn, pydantic, torch, torchvision, cv2, PIL, numpy, sklearn';
      execSync(`"${pythonBin}" -c "${checkCode}"`, { stdio: 'pipe', timeout: 6000 });
    } catch {
      console.log('[Supervisor] Missing libraries detected. Executing automated bootstrapper...');
      const appRoot = this.getAppRoot();
      const bootstrapScript = path.join(appRoot, 'scripts', 'bootstrap_env.py');
      if (fs.existsSync(bootstrapScript)) {
        try {
          execSync(`"${pythonBin}" "${bootstrapScript}"`, { stdio: 'inherit', timeout: 300000 });
          console.log('[Supervisor] Automated dependency installation succeeded.');
        } catch (bootstrapErr: any) {
          console.warn('[Supervisor] Automated dependency installation failed:', bootstrapErr.message);
        }
      }
    }
  }

  private resolveBackendScript(): string {
    if (this.config.backendScriptPath && fs.existsSync(this.config.backendScriptPath)) {
      return this.config.backendScriptPath;
    }

    const app = getElectronApp();
    const appRoot = this.getAppRoot();
    if (app && app.isPackaged) {
      const packagedScript = path.join(process.resourcesPath, 'backend', 'main.py');
      if (fs.existsSync(packagedScript)) {
        return packagedScript;
      }
    }

    const devScript = path.join(appRoot, 'backend', 'main.py');
    if (fs.existsSync(devScript)) {
      return devScript;
    }

    return path.resolve(process.cwd(), 'backend', 'main.py');
  }

  private resolveProjectDir(): string {
    if (this.config.projectDir) {
      return this.config.projectDir;
    }

    const app = getElectronApp();
    if (app && (app.isPackaged || process.env.VISION_AI_STUDIO_USER_DATA_DIR)) {
      // Isolated development QA and production write to userData to guarantee write permissions
      const userProjects = path.join(app.getPath('userData'), 'projects');
      fs.mkdirSync(userProjects, { recursive: true });
      return userProjects;
    }

    const devProjects = path.join(this.getAppRoot(), 'projects');
    fs.mkdirSync(devProjects, { recursive: true });
    return devProjects;
  }

  private getAppRoot(): string {
    const app = getElectronApp();
    if (app) {
      return app.isPackaged ? process.resourcesPath : app.getAppPath();
    }
    return process.cwd();
  }

  private getBackendWorkingDirectory(appRoot: string): string {
    const app = getElectronApp();
    if (!app?.isPackaged) {
      return appRoot;
    }
    const userData = app.getPath('userData');
    fs.mkdirSync(userData, { recursive: true });
    return userData;
  }

  private setState(newState: ProcessState): void {
    if (this.state !== newState) {
      this.state = newState;
      this.emit('status-change', this.getStatusInfo());
    }
  }

  private appendLog(buffer: string[], line: string): void {
    buffer.push(line);
    if (buffer.length > MAX_LOG_BUFFER_LINES) {
      buffer.shift();
    }
  }

  private cleanupState(): void {
    this.childProcess = null;
    this.port = null;
    this.apiToken = null;
    this.healthInfo = null;
    this.startTime = null;
    this.setState('STOPPED');
  }

  private registerProcessHooks(): void {
    const emergencyKill = () => {
      if (this.childProcess && !this.childProcess.killed) forceStop(this.childProcess);
    };

    process.on('exit', emergencyKill);
    process.on('SIGINT', () => { emergencyKill(); process.exit(130); });
    process.on('SIGTERM', () => { emergencyKill(); process.exit(143); });
  }
}
