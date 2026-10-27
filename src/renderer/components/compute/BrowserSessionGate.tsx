import React,{useEffect,useState} from 'react';
import {browserSession} from '../../services/browserSession';
import {SharedProjectPanel} from './SharedProjectPanel';

/** A retained HttpOnly cookie can read server data, but a new page has no CSRF proof.
 * Reuse the normal account flow before mounting any project/data workspace. */
export const BrowserSessionGate:React.FC<{children:React.ReactNode}>=({children})=>{
  const [authenticated,setAuthenticated]=useState(()=>browserSession.connection()!==null);
  useEffect(()=>browserSession.subscribe(()=>{if(!browserSession.connection())setAuthenticated(false);}),[]);
  const connection=browserSession.connection();
  useEffect(()=>{
    if(!authenticated||!connection)return;
    const timer=setTimeout(()=>{if(!browserSession.connection())setAuthenticated(false);},Math.max(0,connection.expires_at*1000-Date.now()));
    return()=>clearTimeout(timer);
  },[authenticated,connection?.expires_at]);
  if(!browserSession.available()||authenticated&&connection)return <>{children}</>;
  return <main aria-label="브라우저 계정 로그인" className="flex min-h-screen items-center justify-center bg-[#0B0E14] px-6 py-10 text-slate-100">
    <section className="w-full max-w-2xl rounded-lg border border-slate-700 bg-[#101722] p-6">
      <h1 className="text-lg font-semibold">브라우저 계정 로그인</h1>
      <p role="status" className="mt-2 text-sm text-slate-300">계정 로그인 후 프로젝트와 데이터 작업을 시작하세요. 새로고침하거나 세션이 만료되면 다시 로그인해야 합니다.</p>
      <SharedProjectPanel localAuthenticationOnly onAuthenticated={()=>{if(browserSession.connection())setAuthenticated(true);}}/>
    </section>
  </main>;
};
