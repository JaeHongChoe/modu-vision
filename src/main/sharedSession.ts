/** Account capability stays in main-process memory and is sent only to its exact server origin. */
export interface SharedConnection {server_url:string;expires_at:number;user:{id:string;username:string;administrator:boolean|number};project_id?:string}
let connection:(SharedConnection & {token:string})|null=null;
export function validatedServerUrl(value:string):string {
  const url=new URL(value);
  if(url.username||url.password||url.search||url.hash||url.pathname!=='/')throw new Error('서버 주소는 인증 정보나 경로 없이 입력하세요.');
  if(url.protocol!=='https:'&&!(url.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(url.hostname)))throw new Error('공유 서버에는 HTTPS 주소를 사용하세요. 로컬 SSH 터널은 HTTP를 사용할 수 있습니다.');
  return url.origin;
}
export function getSharedConnection():SharedConnection|null {if(!connection)return null;const {token,...publicData}=connection;return publicData;}
/** True only for API/WebSocket requests to the connected shared server origin. */
export function isSharedServerUrl(target:string):boolean {
  if(!connection)return false;
  try{
    const url=new URL(target);if(url.protocol==='wss:')url.protocol='https:';if(url.protocol==='ws:')url.protocol='http:';
    return url.origin===connection.server_url&&/^\/(api|ws)\//.test(url.pathname);
  }catch{return false;}
}
export function sharedHeaders(target:string):Record<string,string> {
  if(!connection||connection.expires_at<=Date.now()/1000)return {};
  const url=new URL(target);if(url.protocol==='wss:')url.protocol='https:';if(url.protocol==='ws:')url.protocol='http:';
  if(url.origin!==connection.server_url||!/^\/(api|ws)\//.test(url.pathname))return {};
  return {Authorization:`Bearer ${connection.token}`,...(connection.project_id?{'X-Vision-Project':connection.project_id}:{})};
}
export async function loginSharedServer(input:{server_url:string;username:string;password:string}):Promise<SharedConnection> {
  if(!input||typeof input.username!=='string'||typeof input.password!=='string'||!/^[-A-Za-z0-9_.@]{3,80}$/.test(input.username)||input.password.length<12||input.password.length>1024)throw new Error('계정 이름과 12자 이상의 비밀번호를 확인하세요.');
  const origin=validatedServerUrl(input.server_url);
  const response=await fetch(origin+'/api/accounts/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:input.username,password:input.password}),redirect:'error',signal:AbortSignal.timeout(15000)});
  const value=await response.json();if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:'공유 서버에 로그인하지 못했습니다.');
  if(typeof value.token!=='string'||!Number.isFinite(value.expires_at)||value.expires_at<=Date.now()/1000||!value.user?.id)throw new Error('서버가 유효한 계정 세션을 반환하지 않았습니다.');
  connection={server_url:origin,token:value.token,expires_at:value.expires_at,user:value.user};return getSharedConnection()!;
}
export function selectSharedProject(project_id:string):SharedConnection {
  if(!connection||!/^[A-Za-z0-9_-]{1,128}$/.test(project_id))throw new Error('공유 프로젝트 선택을 확인하세요.');
  connection.project_id=project_id;return getSharedConnection()!;
}
export async function disconnectSharedServer():Promise<void> {
  const previous=connection;connection=null;
  if(previous)await fetch(previous.server_url+'/api/accounts/logout',{method:'POST',headers:{Authorization:`Bearer ${previous.token}`},redirect:'error',signal:AbortSignal.timeout(5000)}).catch(()=>undefined);
}
