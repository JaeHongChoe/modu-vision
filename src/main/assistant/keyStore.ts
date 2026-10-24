/** Assistant provider settings and keys, held by the Electron main process only (§4.4).
 *
 * One entry per provider in `<userData>/assistant/providers.json`: base URL, model, request format, whether the model
 * takes images, and the key encrypted with the OS protection Electron's `safeStorage` uses (Keychain, DPAPI, the Linux
 * secret service). The file is owner-only (0600 in a 0700 folder) and replaced atomically (temporary file, fsync,
 * rename). The renderer only ever sees `hasKey`; the key never reaches logs, the backend, project files or URLs. When
 * the OS protection is unavailable — including Linux's `basic_text` fallback, which is a fixed obfuscation — a key is
 * refused rather than written in a weaker form.
 *
 * Dependency-free apart from Node: the encryptor is passed in, so node tests use a fake one. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {
  AssistantApiFormat, AssistantConfigState, AssistantApiKeyProviderId as AssistantProviderId, AssistantProviderInput, AssistantProviderPublic,
} from '../../types/electron';
import {ProviderError, assistantError, providerLabel, validateBaseUrl, type ProviderConfig} from './providers';

export interface KeyEncryptor {isAvailable():boolean;encrypt(plain:string):Buffer;decrypt(cipher:Buffer):string}

/** The subset of Electron's safeStorage this module uses (kept structural so this file never imports electron). */
export interface SafeStorageLike {
  isEncryptionAvailable():boolean;encryptString(plain:string):Buffer;decryptString(cipher:Buffer):string;
  getSelectedStorageBackend?():string;
}
export function electronEncryptor(storage:SafeStorageLike, platform:string=process.platform):KeyEncryptor {
  return {
    isAvailable: () => storage.isEncryptionAvailable()
      && !(platform === 'linux' && storage.getSelectedStorageBackend?.() === 'basic_text'),
    encrypt: plain => storage.encryptString(plain),
    decrypt: cipher => storage.decryptString(cipher),
  };
}

const PROVIDERS:AssistantProviderId[] = ['openai-compatible', 'anthropic'];
const FILE = 'providers.json';
const MAX_FILE_BYTES = 64 * 1024;
interface StoredEntry {provider:AssistantProviderId;baseUrl:string;model:string;format:AssistantApiFormat|null;supportsImages:boolean;key?:string;updatedAt:string}
/** Which kind of receiver the person last chose explicitly. Not a secret: no account, label or key is stored with it. */
export type AssistantRecipient = 'api' | 'chatgpt';
interface StoredFile {schema_version:1;active:AssistantProviderId|null;providers:Partial<Record<AssistantProviderId, StoredEntry>>;recipient?:AssistantRecipient}

const isProvider = (value:unknown):value is AssistantProviderId => PROVIDERS.includes(value as AssistantProviderId);

function validModel(value:unknown):string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9._:/@+-]{1,200}$/.test(value.trim())) throw new Error('모델 이름을 확인하세요. 목록에서 고르거나 공급자 문서의 모델 ID를 그대로 넣으세요.');
  return value.trim();
}
function validKey(value:unknown):string {
  if (typeof value !== 'string') throw new Error('키 형식을 확인하세요.');
  const key = value.trim();
  if (key.length < 8 || key.length > 4096 || /[^\x21-\x7e]/.test(key)) throw new Error('키 형식을 확인하세요. 공급자 콘솔에서 복사한 키를 공백 없이 넣으세요.');
  return key;
}

export class AssistantKeyStore {
  constructor(private readonly directory:string, private readonly encryptor:KeyEncryptor) {}

  get file():string {return path.join(this.directory, FILE);}

  private read():StoredFile {
    const empty:StoredFile = {schema_version: 1, active: null, providers: {}};
    let stat:fs.Stats;
    try {stat = fs.lstatSync(this.file);} catch {return empty;}
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MAX_FILE_BYTES) throw new Error('도우미 설정 파일을 읽을 수 없습니다. 설정을 다시 저장하세요.');
    let value:any;
    try {value = JSON.parse(fs.readFileSync(this.file, 'utf8'));} catch {throw new Error('도우미 설정 파일이 손상되었습니다. 설정을 다시 저장하세요.');}
    if (value?.schema_version !== 1 || typeof value.providers !== 'object' || !value.providers) throw new Error('도우미 설정 파일 형식을 알 수 없습니다. 설정을 다시 저장하세요.');
    const providers:StoredFile['providers'] = {};
    for (const id of PROVIDERS) {
      const entry = value.providers[id];
      if (entry && typeof entry === 'object' && entry.provider === id) providers[id] = entry;
    }
    const active:unknown = value.active;
    return {schema_version: 1, active: isProvider(active) && providers[active] ? active : null, providers,
      ...(value.recipient === 'api' || value.recipient === 'chatgpt' ? {recipient: value.recipient} : {})};
  }

  /** Temporary file created owner-only, flushed, then renamed over the old one: a crash leaves the old or the new file. */
  private write(data:StoredFile):void {
    fs.mkdirSync(this.directory, {recursive: true, mode: 0o700});
    try {fs.chmodSync(this.directory, 0o700);} catch {/* Windows ACLs: the folder inherits the user's profile */}
    const temporary = path.join(this.directory, `.${FILE}.${process.pid}.${crypto.randomBytes(6).toString('hex')}.tmp`);
    const descriptor = fs.openSync(temporary, 'wx', 0o600);
    try {
      try {
        fs.writeSync(descriptor, JSON.stringify(data, null, 1));
        fs.fsyncSync(descriptor);
      } finally {fs.closeSync(descriptor);}
      fs.renameSync(temporary, this.file);
    } catch (error) {
      try {fs.unlinkSync(temporary);} catch {/* already renamed or never written */}
      throw error;
    }
    try {fs.chmodSync(this.file, 0o600);} catch {/* see above */}
  }

  private publicEntry(entry:StoredEntry):AssistantProviderPublic {
    return {provider: entry.provider, baseUrl: entry.baseUrl, model: entry.model, format: entry.format,
      hasKey: typeof entry.key === 'string' && entry.key.length > 0, supportsImages: entry.supportsImages === true, label: providerLabel(entry)};
  }

  state():AssistantConfigState {
    const data = this.read();
    return {providers: PROVIDERS.flatMap(id => data.providers[id] ? [this.publicEntry(data.providers[id]!)] : []),
      active: data.active, encryptionAvailable: this.encryptor.isAvailable()};
  }

  save(input:AssistantProviderInput):AssistantConfigState {
    if (!input || !isProvider(input.provider)) throw new Error('공급자를 고르세요.');
    const baseUrl = validateBaseUrl(input.baseUrl);
    const model = validModel(input.model);
    const format:AssistantApiFormat|null = input.provider === 'anthropic' ? null : input.format === 'responses' ? 'responses' : 'chat';
    const data = this.read();
    const previous = data.providers[input.provider];
    let key = previous?.key;
    if (input.key !== undefined && input.key !== '') {
      const plain = validKey(input.key);
      if (!this.encryptor.isAvailable()) throw new ProviderError(assistantError('storage_unavailable'));
      key = this.encryptor.encrypt(plain).toString('base64');
    }
    // A key belongs to the address it was entered for: moving the base URL to another host drops it.
    if (previous?.key && input.key === undefined && previous.baseUrl && new URL(previous.baseUrl).origin !== new URL(baseUrl).origin) key = undefined;
    data.providers[input.provider] = {provider: input.provider, baseUrl, model, format, supportsImages: input.supportsImages === true,
      ...(key ? {key} : {}), updatedAt: new Date().toISOString()};
    if (input.activate !== false) data.active = input.provider;
    this.write(data);
    return this.state();
  }

  removeKey(provider:AssistantProviderId):AssistantConfigState {
    const data = this.read();
    const entry = isProvider(provider) ? data.providers[provider] : undefined;
    if (entry?.key) {delete entry.key;entry.updatedAt = new Date().toISOString();this.write(data);}
    return this.state();
  }

  removeProvider(provider:AssistantProviderId):AssistantConfigState {
    const data = this.read();
    if (isProvider(provider) && data.providers[provider]) {
      delete data.providers[provider];
      if (data.active === provider) data.active = PROVIDERS.find(id => data.providers[id]) || null;
      this.write(data);
    }
    return this.state();
  }

  setActive(provider:AssistantProviderId):AssistantConfigState {
    const data = this.read();
    if (!isProvider(provider) || !data.providers[provider]) throw new Error('먼저 이 공급자를 설정하세요.');
    data.active = provider;this.write(data);
    return this.state();
  }

  recipient():AssistantRecipient|null {return this.read().recipient ?? null;}

  setRecipient(recipient:AssistantRecipient):void {
    const data = this.read();
    if (data.recipient !== recipient) {data.recipient = recipient;this.write(data);}
  }

  config(provider?:AssistantProviderId|null):ProviderConfig|null {
    const data = this.read();
    const id = provider ?? data.active;
    const entry = id ? data.providers[id] : undefined;
    return entry ? {provider: entry.provider, baseUrl: entry.baseUrl, model: entry.model, format: entry.format, supportsImages: entry.supportsImages === true} : null;
  }

  /** The decrypted key for one call; main-process only and never cached. */
  key(provider:AssistantProviderId):string|null {
    const entry = this.read().providers[provider];
    if (!entry?.key) return null;
    if (!this.encryptor.isAvailable()) throw new ProviderError(assistantError('storage_unavailable'));
    try {return this.encryptor.decrypt(Buffer.from(entry.key, 'base64'));}
    catch {throw new ProviderError(assistantError('storage_unavailable', {detail: 'decrypt_failed'}));}
  }
}
