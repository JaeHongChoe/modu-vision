/** Shared protected credential ownership, with in-memory recipient selections per session/tab. */
import type {AssistantApiKeyProviderId,AssistantProviderInput,AssistantConfigState} from '../../types/electron';
import type {ProviderConfig} from '../assistant/providers';
import {AssistantKeyStore,type AssistantRecipient} from '../assistant/keyStore';
import {SiwcStore} from '../assistant/siwcStore';
import type {SiwcConnectionStore} from '../assistant/siwcConnection';
export class TabKeyStore {
 private closed=false;
 private check():void {if(this.closed)throw Error('Assistant tab is closed');}
 close():Promise<void>{this.closed=true;return Promise.resolve();}
 private active:AssistantApiKeyProviderId|null=null;private receiver:AssistantRecipient|null=null;
 private models=new Map<AssistantApiKeyProviderId,string>();private initialized:Promise<void>|null=null;
 constructor(private readonly owner:AssistantKeyStore){}
 private initialize():Promise<void>{this.check();return (this.initialized??=this.load()).then(()=>this.check());}
 private async load():Promise<void>{const state=await this.owner.state();this.active=state.active==='chatgpt'?null:state.active;this.receiver=await this.owner.recipient();for(const p of state.providers)if(p.provider!=='chatgpt')this.models.set(p.provider,p.model);}
 async state():Promise<AssistantConfigState>{await this.initialize();const s=await this.owner.state();this.check();return {...s,active:this.active&&s.providers.some(p=>p.provider===this.active)?this.active:s.providers.find(p=>p.provider!=='chatgpt')?.provider as AssistantApiKeyProviderId??null,providers:s.providers.map(p=>({...p,model:p.provider!=='chatgpt'?this.models.get(p.provider)??p.model:p.model}))};}
 async save(input:AssistantProviderInput):Promise<AssistantConfigState>{await this.initialize();await this.owner.save({...input,activate:false},()=>!this.closed);this.check();this.models.set(input.provider,input.model);if(input.activate!==false)this.active=input.provider;return this.state();}
 async removeKey(provider:AssistantApiKeyProviderId):Promise<AssistantConfigState>{this.check();await this.owner.removeKey(provider,()=>!this.closed);this.check();return this.state();}
 async removeProvider(provider:AssistantApiKeyProviderId):Promise<AssistantConfigState>{this.check();await this.owner.removeProvider(provider,()=>!this.closed);this.check();return this.state();}
 async setActive(provider:AssistantApiKeyProviderId):Promise<AssistantConfigState>{await this.initialize();if(!await this.owner.config(provider))throw Error('먼저 이 공급자를 설정하세요.');this.active=provider;return this.state();}
 async recipient():Promise<AssistantRecipient|null>{await this.initialize();return this.receiver;}
 async setRecipient(value:AssistantRecipient):Promise<void>{await this.initialize();this.receiver=value;}
 async config(provider?:AssistantApiKeyProviderId|null):Promise<ProviderConfig|null>{await this.initialize();const id=provider??(await this.state()).active,config=id&&id!=='chatgpt'?await this.owner.config(id):null;return config?{...config,model:this.models.get(config.provider)??config.model}:null;}
 async key(provider:AssistantApiKeyProviderId):Promise<string|null>{return this.owner.key(provider);}
 async credentials(provider?:AssistantApiKeyProviderId|null):Promise<{config:ProviderConfig;key:string|null}|null>{await this.initialize();const id=provider??(await this.state()).active;if(!id||id==='chatgpt')return null;const snapshot=await this.owner.credentials(id);this.check();return snapshot?{...snapshot,config:{...snapshot.config,model:this.models.get(snapshot.config.provider)??snapshot.config.model}}:null;}
}
export function tabSiwcStore(owner:SiwcStore):SiwcConnectionStore {
 let active:string|null=null;const models=new Map<string,string>();
 const state=async()=>{const s=await owner.peekState();return {...s,active,accounts:s.accounts.map(a=>({...a,model:models.get(a.recordId)??a.model}))};};
 return {state,peekState:state,hostId:()=>owner.hostId(),registration:id=>owner.registration(id),credentials:id=>owner.credentials(id??active??undefined),
 async save(tokens,options,current){const a=await owner.save(tokens,{...options,activate:false},current);if(options?.activate!==false)active=a.recordId;return {...a,model:models.get(a.recordId)??a.model};},
 clearCredentials:id=>owner.clearCredentials(id),setActive:async(id,current)=>{if(id!==null&&!await owner.registration(id))throw Error('Unknown account');if(current&&!current())throw Error('Cancelled selection');active=id;},
 setModel:async(id,model,current)=>{if(!await owner.registration(id))throw Error('Unknown account');if(current&&!current())throw Error('Cancelled selection');models.set(id,model);},
 acknowledgePlanUsage:(id,current)=>owner.acknowledgePlanUsage(id,current),setUsagePaused:(id,paused,current)=>owner.setUsagePaused(id,paused,current),
 recoveryStatus:()=>owner.recoveryStatus(),recover:(input,current)=>owner.recover(input,current)};
}
