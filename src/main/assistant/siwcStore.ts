/** Main-process-only ChatGPT account persistence. Even registration identity and host ID live inside the OS-encrypted
 * payload. Public state exposes opaque record IDs and user-selected labels/models. A lifetime exclusive lease prevents
 * competing application processes from rotating the same refresh token. Stale leases are refused, never auto-deleted. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {KeyEncryptor} from './keyStore';
import type {SiwcIdentity, SiwcTokens} from './siwcProtocol';

type StoreCode = 'storage_unavailable' | 'store_locked' | 'store_corrupt' | 'unsafe_path' | 'unknown_account'
  | 'invalid_record' | 'store_closed' | 'store_write_failed' | 'recovery_changed' | 'recovery_unavailable' | 'recovery_confirmation_required';
export class SiwcStoreError extends Error {
  constructor(readonly code: StoreCode) {super(`ChatGPT protected store: ${code}`);this.name = 'SiwcStoreError';}
}
export interface SiwcAccountPublic {recordId: string; label: string; model: string; signedIn: boolean; planUsageEnabled: boolean; planUsageAcknowledged?: boolean; usagePaused?: boolean}
export interface SiwcStoreState {accounts: SiwcAccountPublic[]; active: string | null; encryptionAvailable: boolean}
interface Account {recordId: string; label: string; model: string; identity: SiwcIdentity; tokens?: SiwcTokens; planUsageAcknowledged?: boolean; usagePaused?: boolean}
interface Payload {schema_version: 1; hostId: string; active: string | null; accounts: Account[]}
interface Lease {descriptor: number; nonce: string; dev: number; ino: number; requiresClearing?: boolean}
export interface SiwcRecoveryState {available: boolean; reason: 'none' | 'active_owner' | 'unverifiable_owner' | 'unsafe_store' | 'closed' | 'encryption_unavailable' | 'stale_owner' | 'unavailable_authority'; ticket?: string}
interface RecoverySnapshot {lock: {raw: string; dev: number; ino: number}; file: {raw: string; dev: number; ino: number} | null; pid: number}
const FILE = 'chatgpt-sessions.json', LOCK = 'chatgpt-sessions.lock';
const MAX_FILE = 8 * 1024 * 1024, MAX_PAYLOAD = 2 * 1024 * 1024, MAX_ACCOUNTS = 20;
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
const NOFOLLOW = fs.constants.O_NOFOLLOW || 0;
const fail = (code: StoreCode): never => {throw new SiwcStoreError(code);};
const plainObject = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const keys = (value: Record<string, unknown>, allowed: string[]) => Object.keys(value).every(key => allowed.includes(key));
const text = (value: unknown, max: number): value is string => typeof value === 'string' && value.length > 0 && value.length <= max && !/[\u0000-\u001f\u007f]/.test(value);
const model = (value: unknown): value is string => value === '' || typeof value === 'string' && /^[A-Za-z0-9._:/@+-]{1,200}$/.test(value);
function identity(value: unknown): value is SiwcIdentity {
  return plainObject(value) && keys(value, ['issuer', 'subject', 'clientId']) && value.issuer === 'https://auth.openai.com'
    && text(value.subject, 1000) && typeof value.clientId === 'string' && /^oaiapp_[A-Za-z0-9_-]{1,200}$/.test(value.clientId);
}
const sameIdentity = (a: SiwcIdentity, b: SiwcIdentity) => a.issuer === b.issuer && a.subject === b.subject && a.clientId === b.clientId;
function validTokens(value: unknown): value is SiwcTokens {
  if (!plainObject(value) || !keys(value, ['issuer', 'subject', 'clientId', 'accessToken', 'refreshToken', 'idToken', 'scopes', 'expiresAt', 'planUsageEnabled'])) return false;
  return identity({issuer: value.issuer, subject: value.subject, clientId: value.clientId})
    && text(value.accessToken, 16384) && text(value.refreshToken, 16384) && text(value.idToken, 65536)
    && Array.isArray(value.scopes) && value.scopes.length <= 256 && value.scopes.every(scope => text(scope, 200) && !/\s/.test(scope))
    && new Set(value.scopes).size === value.scopes.length && Number.isSafeInteger(value.expiresAt) && Number(value.expiresAt) > 0
    && typeof value.planUsageEnabled === 'boolean' && value.planUsageEnabled === value.scopes.includes('chatgpt.tokens.use.direct');
}
function validPayload(value: unknown): value is Payload {
  if (!plainObject(value) || !keys(value, ['schema_version', 'hostId', 'active', 'accounts']) || value.schema_version !== 1
    || typeof value.hostId !== 'string' || !value.hostId.startsWith('urn:uuid:') || !UUID.test(value.hostId.slice(9))
    || !Array.isArray(value.accounts) || value.accounts.length > MAX_ACCOUNTS) return false;
  const ids = new Set<string>(), identities = new Set<string>();
  for (const item of value.accounts) {
    if (!plainObject(item) || !keys(item, ['recordId', 'label', 'model', 'identity', 'tokens', 'planUsageAcknowledged', 'usagePaused']) || typeof item.recordId !== 'string'
      || !UUID.test(item.recordId) || !text(item.label, 80) || item.label.trim() !== item.label || !model(item.model) || !identity(item.identity)
      || (item.planUsageAcknowledged !== undefined && typeof item.planUsageAcknowledged !== 'boolean') || (item.usagePaused !== undefined && typeof item.usagePaused !== 'boolean')
      || (item.tokens !== undefined && (!validTokens(item.tokens) || !sameIdentity(item.identity, item.tokens)))) return false;
    const binding = JSON.stringify([item.identity.issuer, item.identity.subject, item.identity.clientId]);
    if (ids.has(item.recordId) || identities.has(binding)) return false;
    ids.add(item.recordId);identities.add(binding);
  }
  return value.active === null || typeof value.active === 'string' && ids.has(value.active);
}

export class SiwcStore {
  private pending:Promise<unknown> = Promise.resolve();
  private check(current?:()=>boolean):void {if(this.closed||(current&&!current())) fail('store_closed');}
  private fence():void {this.check();this.ownsLease();}
  private transaction<T>(run:()=>Promise<T>):Promise<T> {
    const result=this.pending.then(async()=>{this.check();const value=await run();this.check();return value;});
    this.pending=result.catch(()=>undefined);return result;
  }
  private lease: Lease | null = null;
  private closed = false;
  private opened = false;
  private recovery: {ticket: string; snapshot: RecoverySnapshot; expires: number} | null = null;
  private readonly directory: string;
  constructor(directory: string, private readonly encryptor: KeyEncryptor, private readonly options: {canRecover?: () => boolean} = {}) {this.directory = path.resolve(directory);}
  get file(): string {return path.join(this.directory, FILE);}
  get lockFile(): string {return path.join(this.directory, LOCK);}

  /** No symlinks in the target directory or its ancestors; missing components are created individually. */
  private directorySafe(create = false): void {
    let current = path.parse(this.directory).root;
    for (const part of this.directory.slice(current.length).split(path.sep).filter(Boolean)) {
      current = path.join(current, part);
      let stat: fs.Stats;
      try {stat = fs.lstatSync(current);} catch (error) {
        if (!create || (error as NodeJS.ErrnoException).code !== 'ENOENT') return fail('unsafe_path');
        try {fs.mkdirSync(current, {mode: 0o700});stat = fs.lstatSync(current);} catch {return fail('unsafe_path');}
      }
      if (!stat.isDirectory() || stat.isSymbolicLink()) fail('unsafe_path');
    }
    try {
      const stat = fs.lstatSync(this.directory);
      if (process.platform !== 'win32' && stat.uid !== process.getuid?.()) fail('unsafe_path');
      if (create) fs.chmodSync(this.directory, 0o700);
    } catch {fail('unsafe_path');}
  }
  private targetSafe(file: string, missing = true): fs.Stats | null {
    try {
      const stat = fs.lstatSync(file);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1
        || (process.platform !== 'win32' && (stat.uid !== process.getuid?.() || (stat.mode & 0o077) !== 0))) fail('unsafe_path');
      return stat;
    } catch (error) {if (missing && (error as NodeJS.ErrnoException).code === 'ENOENT') return null;return fail('unsafe_path');}
  }
  private ownsLease(): void {
    const lease = this.lease;if (!lease) return fail('store_locked');
    this.directorySafe();
    let descriptor: number | undefined;
    try {
      const stat = this.targetSafe(this.lockFile, false)!;
      if (stat.dev !== lease.dev || stat.ino !== lease.ino || stat.size > 512) fail('store_locked');
      descriptor = fs.openSync(this.lockFile, fs.constants.O_RDONLY | NOFOLLOW);
      const actual = fs.fstatSync(descriptor);
      if (actual.dev !== stat.dev || actual.ino !== stat.ino || fs.readFileSync(descriptor, 'utf8') !== lease.nonce) fail('store_locked');
    } catch {fail('store_locked');} finally {if (descriptor !== undefined) fs.closeSync(descriptor);}
  }
  private releaseLease(): void {
    if (!this.lease) return;
    try {if (!this.lease.requiresClearing) {this.ownsLease();fs.unlinkSync(this.lockFile);}} catch {/* Another owner or unsafe replacement stays untouched. */}
    try {fs.closeSync(this.lease.descriptor);} catch {/* already closed */}
    this.lease = null;
  }
  private async openInternal(current?:()=>boolean): Promise<void> {
    this.check(current);if (this.closed) fail('store_closed');
    if (!(await this.encryptor.isAvailable())) fail('storage_unavailable');this.check(current);
    if (this.opened) {this.fence();return;}
    this.directorySafe(true);
    const nonce = JSON.stringify({schema_version: 2, pid: process.pid, nonce: crypto.randomBytes(32).toString('hex')});
    let descriptor: number;
    try {descriptor = fs.openSync(this.lockFile, fs.constants.O_RDWR | fs.constants.O_CREAT | fs.constants.O_EXCL | NOFOLLOW, 0o600);}
    catch {return fail('store_locked');}
    const stat = fs.fstatSync(descriptor);this.lease = {descriptor, nonce, dev: stat.dev, ino: stat.ino};
    try {
      fs.writeSync(descriptor, nonce);fs.fsyncSync(descriptor);this.ownsLease();
      if (!this.targetSafe(this.file)) (await this.write({schema_version: 1, hostId: `urn:uuid:${crypto.randomUUID()}`, active: null, accounts: []},current));
      else (await this.read());
      this.fence();this.check(current);this.opened = true;
    } catch (error) {this.releaseLease();if (error instanceof SiwcStoreError) throw error;fail('store_write_failed');}
  }
  open(): Promise<void> {return this.transaction(() => this.openInternal());}

  close(retainLease=false): Promise<void> {
    this.closed=true;this.opened=false;this.recovery=null;
    if(retainLease){if(this.lease){try{fs.closeSync(this.lease.descriptor);}catch{}this.lease=null;}return Promise.resolve();}
    this.releaseLease();return Promise.resolve();
  }
  /** Recovery is a user action available only to the main process holding Electron's app instance lock.
   * PID liveness is conservative: only ESRCH is proof of death; old nonce-only leases remain unprovable.
   * Ordinary open() never removes a retained lease. The instance lock serializes official recovery callers;
   * competing ordinary store owners cannot enter until uncertain credentials have been cleared. */
  private async recoveryStatusInternal(): Promise<SiwcRecoveryState> {
    if (this.closed) return {available: false, reason: 'closed'};
    if (!(await this.encryptor.isAvailable())) return {available: false, reason: 'encryption_unavailable'};
    if (this.opened) return {available: false, reason: 'none'};
    if (!this.options.canRecover?.()) return {available: false, reason: 'unavailable_authority'};
    try {
      this.directorySafe();const snapshot = this.recoverySnapshot();
      if (!snapshot) return {available: false, reason: 'none'};
      const owner = this.ownerState(snapshot.pid);
      if (owner !== 'stale_owner') {this.recovery = null;return {available: false, reason: owner};}
      if (!this.recovery || this.recovery.expires <= Date.now() || JSON.stringify(this.recovery.snapshot) !== JSON.stringify(snapshot))
        this.recovery = {ticket: crypto.randomBytes(32).toString('hex'), snapshot, expires: Date.now() + 300000};
      return {available: true, reason: 'stale_owner', ticket: this.recovery.ticket};
    } catch (error) {this.recovery = null;return {available: false, reason: error instanceof SiwcStoreError && error.code === 'recovery_unavailable' ? 'unverifiable_owner' : 'unsafe_store'};}
  }
  recoveryStatus(): Promise<SiwcRecoveryState> {return this.transaction(() => this.recoveryStatusInternal());}

  private ownerState(pid: number): 'active_owner' | 'unverifiable_owner' | 'stale_owner' {
    try {process.kill(pid, 0);return 'active_owner';} catch (error) {return (error as NodeJS.ErrnoException).code === 'ESRCH' ? 'stale_owner' : 'unverifiable_owner';}
  }
  private snapshotFile(file: string, max: number): {raw: string; dev: number; ino: number} | null {
    const stat = this.targetSafe(file);if (!stat) return null;if (stat.size > max) fail('unsafe_path');
    let descriptor: number | undefined;
    try {descriptor = fs.openSync(file, fs.constants.O_RDONLY | NOFOLLOW);const actual = fs.fstatSync(descriptor);
      if (actual.dev !== stat.dev || actual.ino !== stat.ino || actual.size > max) fail('unsafe_path');
      const raw = fs.readFileSync(descriptor, 'utf8');if (Buffer.byteLength(raw) > max) fail('unsafe_path');return {raw, dev: actual.dev, ino: actual.ino};
    } finally {if (descriptor !== undefined) fs.closeSync(descriptor);}
  }
  private recoverySnapshot(): RecoverySnapshot | null {
    const lock = this.snapshotFile(this.lockFile, 512);if (!lock) return null;
    let value: unknown;try {value = JSON.parse(lock.raw);} catch {return fail('recovery_unavailable');}
    if (!plainObject(value) || !keys(value, ['schema_version', 'pid', 'nonce']) || value.schema_version !== 2
      || !Number.isSafeInteger(value.pid) || Number(value.pid) < 1 || Number(value.pid) > 2147483647
      || typeof value.nonce !== 'string' || !/^[a-f0-9]{64}$/.test(value.nonce)) return fail('recovery_unavailable');
    return {lock, file: this.snapshotFile(this.file, MAX_FILE), pid: Number(value.pid)};
  }
  private async recoverInternal(input: {ticket: string; confirmed: boolean}, current?:()=>boolean): Promise<void> {
    this.check(current);if (input?.confirmed !== true) fail('recovery_confirmation_required');
    if (this.closed || this.opened || !this.options.canRecover?.() || !(await this.encryptor.isAvailable())) fail('recovery_unavailable');this.check(current);
    const preview = this.recovery;
    if (!preview || preview.ticket !== input.ticket || preview.expires <= Date.now()) return fail('recovery_changed');
    this.directorySafe();const snapshot = this.recoverySnapshot();
    if (!snapshot || JSON.stringify(snapshot) !== JSON.stringify(preview.snapshot)) return fail('recovery_changed');
    if (this.ownerState(snapshot.pid) !== 'stale_owner') fail('recovery_unavailable');
    let descriptor: number | undefined;
    try {
      descriptor = fs.openSync(this.lockFile, fs.constants.O_RDWR | NOFOLLOW);const stat = fs.fstatSync(descriptor);
      if (stat.dev !== snapshot.lock.dev || stat.ino !== snapshot.lock.ino || fs.readFileSync(descriptor, 'utf8') !== snapshot.lock.raw) fail('recovery_changed');
      this.lease = {descriptor, nonce: snapshot.lock.raw, dev: stat.dev, ino: stat.ino, requiresClearing: true};descriptor = undefined;
      // Keep the stale lease throughout validation, archive and atomic credential clearing. A new owner
      // must never see old refresh tokens after this recovery releases the retained lock.
      if (snapshot.file) {
        const data = (await this.read());this.fence();this.check(current);
        if (JSON.stringify(this.snapshotFile(this.file, MAX_FILE)) !== JSON.stringify(snapshot.file)) fail('recovery_changed');
        const archive = path.join(this.directory, `chatgpt-sessions.recovery-${crypto.randomUUID()}.json`);
        let archiveFd: number | undefined;
        try {archiveFd = fs.openSync(archive, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | NOFOLLOW, 0o600);
          fs.writeFileSync(archiveFd, snapshot.file.raw);fs.fsyncSync(archiveFd);
        } finally {if (archiveFd !== undefined) fs.closeSync(archiveFd);}
        for (const account of data.accounts) delete account.tokens;
        if (!this.options.canRecover?.() || this.ownerState(snapshot.pid) !== 'stale_owner') fail('recovery_unavailable');
        (await this.write(data,current));
      }
      // Only completed clearing permits releasing this adopted stale lease. Close during a
      // crypto await retains it, even when the connection has no in-memory refresh session.
      this.fence();this.check(current);this.lease!.requiresClearing = false;
      this.recovery = null;this.releaseLease();(await this.openInternal(current));this.check(current);
    } catch (error) {
      // Failure must retain the stale lease. Even close() must not remove it after an incomplete recovery.
      if (this.lease) {try {fs.closeSync(this.lease.descriptor);} catch {}this.lease = null;}
      if (error instanceof SiwcStoreError) throw error;fail('store_write_failed');
    } finally {if (descriptor !== undefined) fs.closeSync(descriptor);}
  }
  recover(input: {ticket: string; confirmed: boolean}, current?:()=>boolean): Promise<void> {return this.transaction(() => this.recoverInternal(input,current));}

  private async ready(current?:()=>boolean): Promise<void> {(await this.openInternal(current));this.check(current);this.ownsLease();if (!(await this.encryptor.isAvailable())) fail('storage_unavailable');this.fence();this.check(current);}
  private async read(): Promise<Payload> {
    this.ownsLease();
    const stat = this.targetSafe(this.file, false)!;
    if (stat.size > MAX_FILE) return fail('store_corrupt');
    let descriptor: number | undefined;
    try {
      descriptor = fs.openSync(this.file, fs.constants.O_RDONLY | NOFOLLOW);
      const actual = fs.fstatSync(descriptor);
      if (actual.dev !== stat.dev || actual.ino !== stat.ino || actual.size > MAX_FILE) return fail('unsafe_path');
      const envelope = JSON.parse(fs.readFileSync(descriptor, 'utf8'));
      if (!plainObject(envelope) || !keys(envelope, ['schema_version', 'payload']) || envelope.schema_version !== 1
        || typeof envelope.payload !== 'string' || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(envelope.payload)
        || !envelope.payload.length) return fail('store_corrupt');
      const decrypted = (await this.encryptor.decrypt(Buffer.from(envelope.payload, 'base64')));this.fence();
      if (Buffer.byteLength(decrypted) > MAX_PAYLOAD) return fail('store_corrupt');
      const value = JSON.parse(decrypted);
      if (!validPayload(value)) return fail('store_corrupt');
      return value;
    } catch (error) {if (error instanceof SiwcStoreError) throw error;return fail('store_corrupt');}
    finally {if (descriptor !== undefined) fs.closeSync(descriptor);}
  }
  private async write(data: Payload, current?:()=>boolean): Promise<void> {
    this.ownsLease();this.check(current);
    if (!(await this.encryptor.isAvailable())) fail('storage_unavailable');this.check(current);
    if (!validPayload(data)) fail('invalid_record');
    const plain = JSON.stringify(data);if (Buffer.byteLength(plain) > MAX_PAYLOAD) fail('invalid_record');
    const temporary = path.join(this.directory, `.${FILE}.${crypto.randomBytes(12).toString('hex')}.tmp`);
    let descriptor: number | undefined;
    try {
      const payload = (await this.encryptor.encrypt(plain));this.fence();if(current&&!current())fail('store_closed');
      if (!Buffer.isBuffer(payload) || !payload.length || payload.equals(Buffer.from(plain))) fail('storage_unavailable');
      const raw = JSON.stringify({schema_version: 1, payload: payload.toString('base64')});if (Buffer.byteLength(raw) > MAX_FILE) fail('invalid_record');
      descriptor = fs.openSync(temporary, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | NOFOLLOW, 0o600);
      fs.writeFileSync(descriptor, raw);fs.fsyncSync(descriptor);fs.closeSync(descriptor);descriptor = undefined;
      this.fence();this.check(current);this.targetSafe(this.file);fs.renameSync(temporary, this.file);
    } catch (error) {
      if (descriptor !== undefined) {try {fs.closeSync(descriptor);} catch {/* write failed */}}
      try {fs.unlinkSync(temporary);} catch {/* no temporary was created */}
      if (error instanceof SiwcStoreError) throw error;
      fail('store_write_failed');
    }
  }
  private publicAccount(account: Account): SiwcAccountPublic {
    return {recordId: account.recordId, label: account.label, model: account.model, signedIn: !!account.tokens,
      planUsageEnabled: account.tokens?.planUsageEnabled === true,
      ...(account.planUsageAcknowledged !== undefined ? {planUsageAcknowledged: account.planUsageAcknowledged} : {}),
      ...(account.usagePaused !== undefined ? {usagePaused: account.usagePaused} : {})};
  }
  /** Reading the general API-key settings must not create a ChatGPT host registration or acquire its lease. */
  private async peekStateInternal(): Promise<SiwcStoreState> {
    if (this.closed) fail('store_closed');
    if (!this.opened) {
      try {fs.lstatSync(this.file);} catch (error) {
        if ((error as NodeJS.ErrnoException).code === 'ENOENT') return {accounts: [], active: null, encryptionAvailable: (await this.encryptor.isAvailable())};
        return fail('unsafe_path');
      }
    }
    return (await this.stateInternal());
  }
  peekState(): Promise<SiwcStoreState> {return this.transaction(() => this.peekStateInternal());}

  private async stateInternal(): Promise<SiwcStoreState> {
    if (this.closed) fail('store_closed');
    if (!(await this.encryptor.isAvailable())) return {accounts: [], active: null, encryptionAvailable: false};
    (await this.ready());const data = (await this.read());
    return {accounts: data.accounts.map(account => this.publicAccount(account)), active: data.active, encryptionAvailable: true};
  }
  state(): Promise<SiwcStoreState> {return this.transaction(() => this.stateInternal());}

  private async hostIdInternal(): Promise<string> {(await this.ready());return (await this.read()).hostId;}
  hostId(): Promise<string> {return this.transaction(() => this.hostIdInternal());}

  private async registrationInternal(recordId: string): Promise<SiwcIdentity | null> {(await this.ready());return (await this.read()).accounts.find(account => account.recordId === recordId)?.identity || null;}
  registration(recordId: string): Promise<SiwcIdentity | null> {return this.transaction(() => this.registrationInternal(recordId));}

  private async credentialsInternal(recordId?: string): Promise<SiwcTokens | null> {
    (await this.ready());const data = (await this.read());return data.accounts.find(account => account.recordId === (recordId ?? data.active))?.tokens || null;
  }
  credentials(recordId?: string): Promise<SiwcTokens | null> {return this.transaction(() => this.credentialsInternal(recordId));}

  private async saveInternal(tokens: SiwcTokens, options: {recordId?: string; label?: string; activate?: boolean} = {}, current?:()=>boolean): Promise<SiwcAccountPublic> {
    if (!validTokens(tokens) || !plainObject(options) || !keys(options, ['recordId', 'label', 'activate'])
      || (options.recordId !== undefined && (typeof options.recordId !== 'string' || !UUID.test(options.recordId)))
      || (options.label !== undefined && (!text(options.label, 80) || options.label.trim() !== options.label))
      || (options.activate !== undefined && typeof options.activate !== 'boolean')) fail('invalid_record');
    (await this.ready());const data = (await this.read());
    const identity: SiwcIdentity = {issuer: tokens.issuer, subject: tokens.subject, clientId: tokens.clientId};
    let account = options.recordId ? data.accounts.find(item => item.recordId === options.recordId) : data.accounts.find(item => sameIdentity(item.identity, identity));
    if (options.recordId && (!account || !sameIdentity(account.identity, identity))) fail('invalid_record');
    if (!account) {
      if (data.accounts.length >= MAX_ACCOUNTS) fail('invalid_record');
      account = {recordId: crypto.randomUUID(), identity, label: options.label || `ChatGPT 계정 ${data.accounts.length + 1}`, model: ''};
      data.accounts.push(account);
    }
    account.tokens = structuredClone(tokens);if (options.label !== undefined) account.label = options.label;
    if (options.activate !== false) data.active = account.recordId;
    if(current&&!current())fail('store_closed');(await this.write(data,current));return this.publicAccount(account);
  }
  save(tokens: SiwcTokens, options: {recordId?: string; label?: string; activate?: boolean} = {}, current?:()=>boolean): Promise<SiwcAccountPublic> {return this.transaction(() => this.saveInternal(tokens,options,current));}

  private async change(recordId: string, update: (account: Account) => void, current?:()=>boolean): Promise<void> {
    this.check(current);(await this.ready(current));this.check(current);const data = (await this.read()), account = data.accounts.find(item => item.recordId === recordId);
    if (!account) return fail('unknown_account');if(current&&!current())fail('store_closed');update(account);(await this.write(data,current));
  }
  private async clearCredentialsInternal(recordId: string): Promise<void> {(await this.change(recordId, account => {delete account.tokens;}));}
  clearCredentials(recordId: string): Promise<void> {return this.transaction(() => this.clearCredentialsInternal(recordId));}

  private async acknowledgePlanUsageInternal(recordId: string, current?:()=>boolean): Promise<void> {(await this.change(recordId, account => {account.planUsageAcknowledged = true;},current));}
  acknowledgePlanUsage(recordId: string, current?:()=>boolean): Promise<void> {return this.transaction(() => this.acknowledgePlanUsageInternal(recordId,current));}

  private async setUsagePausedInternal(recordId: string, paused: boolean, current?:()=>boolean): Promise<void> {
    if (typeof paused !== 'boolean') fail('invalid_record');(await this.change(recordId, account => {account.usagePaused = paused;},current));
  }
  setUsagePaused(recordId: string, paused: boolean, current?:()=>boolean): Promise<void> {return this.transaction(() => this.setUsagePausedInternal(recordId,paused,current));}

  private async setActiveInternal(recordId: string | null, current?:()=>boolean): Promise<void> {
    (await this.ready());const data = (await this.read());if (recordId !== null && !data.accounts.some(account => account.recordId === recordId)) fail('unknown_account');
    if(current&&!current())fail('store_closed');data.active = recordId;(await this.write(data,current));
  }
  setActive(recordId: string | null, current?:()=>boolean): Promise<void> {return this.transaction(() => this.setActiveInternal(recordId,current));}

  private async setModelInternal(recordId: string, value: string, current?:()=>boolean): Promise<void> {
    if (!model(value)) fail('invalid_record');(await this.change(recordId, account => {account.model = value;},current));
  }
  setModel(recordId: string, value: string, current?:()=>boolean): Promise<void> {return this.transaction(() => this.setModelInternal(recordId,value,current));}

  private async setLabelInternal(recordId: string, value: string): Promise<void> {
    if (!text(value, 80) || value.trim() !== value) fail('invalid_record');(await this.change(recordId, account => {account.label = value;}));
  }
  setLabel(recordId: string, value: string): Promise<void> {return this.transaction(() => this.setLabelInternal(recordId,value));}

}
