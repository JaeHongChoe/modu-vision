import type { SharedConnection } from '../../types/electron';
import type { ProjectContext } from './api';
type RemoteConnection=SharedConnection&{remote_id:string;transport_url:string;authority_epoch:number};
type Session={connection:()=>SharedConnection|null;requestOptions:(url:string,method?:string)=>{credentials?:RequestCredentials;headers?:Record<string,string>}};
type Host={prepareRemote:()=>Promise<void>;requestHeaders:(url:string,method?:string)=>Record<string,string>};
/** Only public remote metadata lives here. Cookie identity always belongs to the local account. */
export class BrowserRemote {
 private current:RemoteConnection|null=null;
 private inactive=false;
 private epoch=0;
 private pending=new Set<AbortController>();
 constructor(private readonly runtime:{origin:()=>string|null;session:Session;host:()=>Host|null;fetch:typeof fetch;now:()=>number}){}
 connection():RemoteConnection|null {if(!this.runtime.session.connection()){this.retire();return null;}if(this.current&&this.current.expires_at<=this.runtime.now()&&!this.inactive)this.invalidate();return !this.inactive&&this.current?{...this.current,user:{...this.current.user}}:null;}
 // Selection is cleanup/routing intent only; expired metadata never authorizes requests.
 selection():RemoteConnection|null {this.connection();return this.current?{...this.current,user:{...this.current.user}}:null;}
 private invalidate():void {this.epoch++;this.inactive=true;for(const request of this.pending)request.abort();this.pending.clear();}
 private retire():void {this.invalidate();this.current=null;}
 private tabHeaders():Record<string,string>{const host=this.runtime.host(),origin=this.runtime.origin();if(!host||!origin)return {};return host.requestHeaders(origin+'/api/local-host/remote/login','POST');}
 private async call(path:string,body:unknown,epoch=this.epoch):Promise<any>{
  if(!this.runtime.session.connection())throw new Error('로컬 계정에 먼저 로그인하세요.');
  const url=this.runtime.origin()+'/api/local-host/remote'+path,options=this.runtime.session.requestOptions(url,'POST'),controller=new AbortController();
  this.pending.add(controller);
  try{const response=await this.runtime.fetch(url,{...options,method:'POST',redirect:'error',headers:{'Content-Type':'application/json',...options.headers,...this.tabHeaders()},body:JSON.stringify(body),signal:controller.signal});const value=await response.json();
   if(epoch!==this.epoch||!this.runtime.session.connection())throw new Error('연결 상태가 바뀌었습니다. 다시 연결하세요.');
   if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:'원격 서버 요청을 처리하지 못했습니다.');return value;
  }finally{this.pending.delete(controller);}
 }
 private accept(value:any):RemoteConnection {
  const origin=this.runtime.origin();
  if(!value||'token' in value||typeof value.remote_id!=='string'||!/^[A-Za-z0-9_-]{1,128}$/.test(value.remote_id)||value.transport_url!==origin+'/api/local-host/remote/'+value.remote_id||!Number.isFinite(value.expires_at)||value.expires_at<=this.runtime.now()||typeof value.user?.id!=='string')throw new Error('원격 연결 응답을 확인하세요.');
  this.current={server_url:value.server_url,transport_url:value.transport_url,remote_id:value.remote_id,authority_epoch:value.authority_epoch,expires_at:value.expires_at,user:{id:value.user.id,username:value.user.username,administrator:value.user.administrator},...(value.project_id?{project_id:value.project_id}:{})};this.inactive=false;return this.connection()!;
 }
 async login(input:{server_url:string;username:string;password:string}):Promise<RemoteConnection>{
  const host=this.runtime.host();if(!host)throw new Error('로컬 보조 프로그램 연결을 확인하세요.');
  this.retire();const epoch=this.epoch;await host.prepareRemote();if(epoch!==this.epoch)throw new Error('연결 상태가 바뀌었습니다.');return this.accept(await this.call('/login',input,epoch));
 }
 async select(projectId:string,context?:ProjectContext):Promise<RemoteConnection>{const current=this.connection();if(!current)throw new Error('원격 서버에 로그인하세요.');return this.accept(await this.call('/'+current.remote_id+'/context',{project_id:projectId,context:context??null}));}
 requestOptions(target:string,method='GET'):{credentials?:RequestCredentials;headers?:Record<string,string>}{const current=this.connection();if(!current||!target.startsWith(current.transport_url+'/api/')&&!(target===current.transport_url+'/health'&&/^(GET|HEAD)$/i.test(method)))return {};const options=this.runtime.session.requestOptions(target,method);return {...options,headers:{...options.headers,...this.runtime.host()?.requestHeaders(target,method)}};}
 assetUrl(path:string):string {
  const current=this.connection();if(!current)return '';
  let url:URL;try{url=new URL(path,current.server_url);}catch{return '';}
  if(url.origin!==new URL(current.server_url).origin||!url.pathname.startsWith('/api/'))return '';
  const tab=this.tabHeaders()['X-Vision-Local-Tab'];if(!tab)return '';
  url.searchParams.set('local_tab',tab);return current.transport_url+url.pathname+url.search;
 }
 socketUrl(path:string,context?:ProjectContext):string {const current=this.connection(),tab=this.tabHeaders()['X-Vision-Local-Tab'];if(!current||!tab||!path.startsWith('/ws/'))throw new Error('원격 연결을 확인하세요.');const query=new URLSearchParams({local_tab:tab,...(context?{project_context:JSON.stringify(context)}:{})});return current.transport_url.replace(/^http/,'ws')+path+'?'+query;}
 async disconnect():Promise<void>{const previous=this.selection();this.invalidate();if(previous){await this.call('/'+previous.remote_id+'/logout',{});this.retire();}else this.retire();}
}
