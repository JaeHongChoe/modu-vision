/** Shared protected credential ownership, with in-memory recipient selections per session/tab. */
import type {AssistantApiKeyProviderId,AssistantProviderInput} from '../../types/electron';
import {AssistantKeyStore,type AssistantRecipient} from '../assistant/keyStore';
import {SiwcStore} from '../assistant/siwcStore';
import type {SiwcConnectionStore} from '../assistant/siwcConnection';
export class TabKeyStore {
 private active:AssistantApiKeyProviderId|null;private receiver:AssistantRecipient|null;
 private models=new Map<AssistantApiKeyProviderId,string>();
 constructor(private readonly owner:AssistantKeyStore){const state=owner.state();this.active=state.active==='chatgpt'?null:state.active;this.receiver=owner.recipient();for(const p of state.providers)if(p.provider!=='chatgpt')this.models.set(p.provider,p.model);}
 state(){const s=this.owner.state();return {...s,active:this.active&&s.providers.some(p=>p.provider===this.active)?this.active:s.providers.find(p=>p.provider!=='chatgpt')?.provider as AssistantApiKeyProviderId??null,providers:s.providers.map(p=>({...p,model:p.provider!=='chatgpt'?this.models.get(p.provider)??p.model:p.model}))};}
 save(input:AssistantProviderInput){this.owner.save({...input,activate:false});this.models.set(input.provider,input.model);if(input.activate!==false)this.active=input.provider;return this.state();}
 removeKey(provider:AssistantApiKeyProviderId){this.owner.removeKey(provider);return this.state();}
 removeProvider(provider:AssistantApiKeyProviderId){this.owner.removeProvider(provider);return this.state();}
 setActive(provider:AssistantApiKeyProviderId){if(!this.owner.config(provider))throw Error('먼저 이 공급자를 설정하세요.');this.active=provider;return this.state();}
 recipient(){return this.receiver;}
 setRecipient(value:AssistantRecipient){this.receiver=value;}
 config(provider?:AssistantApiKeyProviderId|null){const id=provider??this.state().active,config=id?this.owner.config(id):null;return config?{...config,model:this.models.get(config.provider)??config.model}:null;}
 key(provider:AssistantApiKeyProviderId){return this.owner.key(provider);}
}
export function tabSiwcStore(owner:SiwcStore):SiwcConnectionStore {
 let active:string|null=null;const models=new Map<string,string>();
 const state=()=>{const s=owner.peekState();return {...s,active,accounts:s.accounts.map(a=>({...a,model:models.get(a.recordId)??a.model}))};};
 return {state,peekState:state,hostId:()=>owner.hostId(),registration:id=>owner.registration(id),credentials:id=>owner.credentials(id??active??undefined),
 save(tokens,options){const a=owner.save(tokens,{...options,activate:false});if(options?.activate!==false)active=a.recordId;return {...a,model:models.get(a.recordId)??a.model};},
 clearCredentials:id=>owner.clearCredentials(id),setActive:id=>{if(id!==null&&!owner.registration(id))throw Error('Unknown account');active=id;},
 setModel:(id,model)=>{if(!owner.registration(id))throw Error('Unknown account');models.set(id,model);},
 acknowledgePlanUsage:id=>owner.acknowledgePlanUsage(id),setUsagePaused:(id,paused)=>owner.setUsagePaused(id,paused),
 recoveryStatus:()=>owner.recoveryStatus(),recover:input=>owner.recover(input)};
}
