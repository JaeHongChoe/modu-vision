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

export type MaybePromise<T> = T | Promise<T>;
export interface KeyEncryptor {isAvailable():MaybePromise<boolean>;encrypt(plain:string):MaybePromise<Buffer>;decrypt(cipher:Buffer):MaybePromise<string>}

/** The subset of Electron's safeStorage this module uses (kept structural so this file never imports electron). */
export interface SafeStorageLike {
  isEncryptionAvailable():boolean;encryptString(plain:string):Buffer;decryptString(cipher:Buffer):string;
  getSelectedStorageBackend?():string;
  isAsyncEncryptionAvailable?():Promise<boolean>;encryptStringAsync?(plain:string):Promise<Buffer>;
  decryptStringAsync?(cipher:Buffer):Promise<{result:string;shouldReEncrypt:boolean}>;
}
export function electronEncryptor(storage:SafeStorageLike, platform:string=process.platform):KeyEncryptor {
  if (platform === 'darwin') return {
    isAvailable: async () => !!(storage.isAsyncEncryptionAvailable && storage.encryptStringAsync && storage.decryptStringAsync) && await storage.isAsyncEncryptionAvailable(),
    encrypt: async plain => {if (!storage.encryptStringAsync) throw new ProviderError(assistantError('storage_unavailable'));return storage.encryptStringAsync(plain);},
    decrypt: async cipher => {if (!storage.decryptStringAsync) throw new ProviderError(assistantError('storage_unavailable'));return (await storage.decryptStringAsync(cipher)).result;},
  };
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
  private pending:Promise<unknown> = Promise.resolve();
  private closed = false;
  private check(current?:()=>boolean):void {if(this.closed||(current&&!current())) throw new ProviderError(assistantError('cancelled'));}
  private transaction<T>(run:()=>Promise<T>):Promise<T> {
    const result = this.pending.then(async () => {this.check();const value=await run();this.check();return value;});
    this.pending=result.catch(()=>undefined);return result;
  }
  close():Promise<void> {this.closed=true;return Promise.resolve();}
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
  private write(data:StoredFile,current?:()=>boolean):void {
    this.check(current);
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

  private async stateInternal():Promise<AssistantConfigState> {
    const data = this.read();
    return {providers: PROVIDERS.flatMap(id => data.providers[id] ? [this.publicEntry(data.providers[id]!)] : []),
      active: data.active, encryptionAvailable: (await this.encryptor.isAvailable())};
  }
  state(): Promise<AssistantConfigState> {return this.transaction(() => this.stateInternal());}

  private async saveInternal(input:AssistantProviderInput, current?:()=>boolean):Promise<AssistantConfigState> {
    if(current&&!current())throw new ProviderError(assistantError('cancelled'));
    if (!input || !isProvider(input.provider)) throw new Error('공급자를 고르세요.');
    const baseUrl = validateBaseUrl(input.baseUrl);
    const model = validModel(input.model);
    const format:AssistantApiFormat|null = input.provider === 'anthropic' ? null : input.format === 'responses' ? 'responses' : 'chat';
    const data = this.read();
    const previous = data.providers[input.provider];
    let key = previous?.key;
    if (input.key !== undefined && input.key !== '') {
      const plain = validKey(input.key);
      if (!(await this.encryptor.isAvailable())) throw new ProviderError(assistantError('storage_unavailable'));
      this.check();if(current&&!current())throw new ProviderError(assistantError('cancelled'));
      const encrypted = await this.encryptor.encrypt(plain);this.check();if(current&&!current())throw new ProviderError(assistantError('cancelled'));
      key = encrypted.toString('base64');
    }
    // A key belongs to the address it was entered for: moving the base URL to another host drops it.
    if (previous?.key && (input.key === undefined || input.key === '') && previous.baseUrl && new URL(previous.baseUrl).origin !== new URL(baseUrl).origin) key = undefined;
    data.providers[input.provider] = {provider: input.provider, baseUrl, model, format, supportsImages: input.supportsImages === true,
      ...(key ? {key} : {}), updatedAt: new Date().toISOString()};
    if (input.activate !== false) data.active = input.provider;
    if(current&&!current())throw new ProviderError(assistantError('cancelled'));this.write(data);
    return (await this.stateInternal());
  }
  save(input:AssistantProviderInput, current?:()=>boolean): Promise<AssistantConfigState> {return this.transaction(() => this.saveInternal(input,current));}

  private async removeKeyInternal(provider:AssistantProviderId,current?:()=>boolean):Promise<AssistantConfigState> {
    this.check(current);const data = this.read();
    const entry = isProvider(provider) ? data.providers[provider] : undefined;
    if (entry?.key) {delete entry.key;entry.updatedAt = new Date().toISOString();this.write(data,current);}
    return (await this.stateInternal());
  }
  removeKey(provider:AssistantProviderId,current?:()=>boolean): Promise<AssistantConfigState> {return this.transaction(() => this.removeKeyInternal(provider,current));}

  private async removeProviderInternal(provider:AssistantProviderId,current?:()=>boolean):Promise<AssistantConfigState> {
    this.check(current);const data = this.read();
    if (isProvider(provider) && data.providers[provider]) {
      delete data.providers[provider];
      if (data.active === provider) data.active = PROVIDERS.find(id => data.providers[id]) || null;
      this.write(data,current);
    }
    return (await this.stateInternal());
  }
  removeProvider(provider:AssistantProviderId,current?:()=>boolean): Promise<AssistantConfigState> {return this.transaction(() => this.removeProviderInternal(provider,current));}

  private async setActiveInternal(provider:AssistantProviderId):Promise<AssistantConfigState> {
    const data = this.read();
    if (!isProvider(provider) || !data.providers[provider]) throw new Error('먼저 이 공급자를 설정하세요.');
    data.active = provider;this.write(data);
    return (await this.stateInternal());
  }
  setActive(provider:AssistantProviderId): Promise<AssistantConfigState> {return this.transaction(() => this.setActiveInternal(provider));}

  private async recipientInternal():Promise<AssistantRecipient|null> {return this.read().recipient ?? null;}
  recipient(): Promise<AssistantRecipient|null> {return this.transaction(() => this.recipientInternal());}

  private async setRecipientInternal(recipient:AssistantRecipient):Promise<void> {
    const data = this.read();
    if (data.recipient !== recipient) {data.recipient = recipient;this.write(data);}
  }
  setRecipient(recipient:AssistantRecipient): Promise<void> {return this.transaction(() => this.setRecipientInternal(recipient));}

  private async configInternal(provider?:AssistantProviderId|null):Promise<ProviderConfig|null> {
    const data = this.read();
    const id = provider ?? data.active;
    const entry = id ? data.providers[id] : undefined;
    return entry ? this.configuration(entry) : null;
  }
  config(provider?:AssistantProviderId|null): Promise<ProviderConfig|null> {return this.transaction(() => this.configInternal(provider));}

  private configuration(entry:StoredEntry):ProviderConfig {
    return {provider:entry.provider,baseUrl:entry.baseUrl,model:entry.model,format:entry.format,supportsImages:entry.supportsImages===true};
  }
  credentials(provider?:AssistantProviderId|null):Promise<{config:ProviderConfig;key:string|null}|null> {
    return this.transaction(async () => {
      // Read exactly one file version. Config and ciphertext stay bound even across an external atomic replacement.
      const data=this.read(),id=provider??data.active,entry=id?data.providers[id]:undefined;
      if(!entry)return null;const config=this.configuration(entry),key=await this.decryptEntry(entry);this.check();return {config,key};
    });
  }
  /** The decrypted key for one call; main-process only and never cached. */
  private async decryptEntry(entry:StoredEntry|undefined):Promise<string|null> {
    if(!entry?.key)return null;
    if(!await this.encryptor.isAvailable())throw new ProviderError(assistantError('storage_unavailable'));this.check();
    let key:string;
    try {key=await this.encryptor.decrypt(Buffer.from(entry.key,'base64'));}
    catch {throw new ProviderError(assistantError('storage_unavailable',{detail:'decrypt_failed'}));}
    this.check();return key;
  }
  private async keyInternal(provider:AssistantProviderId):Promise<string|null> {return this.decryptEntry(this.read().providers[provider]);}

  key(provider:AssistantProviderId): Promise<string|null> {return this.transaction(() => this.keyInternal(provider));}

}
