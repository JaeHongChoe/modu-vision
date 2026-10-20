/** "서버 준비 방법": the shared server set up step by step, with safe defaults (audit F-17 / B4).
 *  The server listens on 127.0.0.1 only; other computers reach it through an SSH tunnel or an HTTPS proxy. */
import { useState } from 'react';
import { setupCommands } from './sharedServerSetup';

export function SharedServerSetupGuide() {
  const [answers, setAnswers] = useState({ accountsDir: '/srv/vision/accounts', projectsDir: '/srv/vision/projects', port: 8000, admin: 'admin', sshTarget: '사용자@서버주소' });
  const [copied, setCopied] = useState('');
  const field = 'min-w-0 rounded border border-slate-600 bg-slate-950 px-2 py-1 text-[11px] text-slate-200';
  const copy = async (command: string) => {
    try { await navigator.clipboard.writeText(command); setCopied(command); } catch { setCopied(''); }
  };
  return <details className="rounded border border-slate-700 p-2 text-[11px] text-slate-300">
    <summary className="cursor-pointer font-semibold">서버 준비 방법 (처음 한 번, 관리자)</summary>
    <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
      <label>계정 저장 폴더 (서버)<input aria-label="계정 저장 폴더" value={answers.accountsDir} onChange={e => setAnswers({ ...answers, accountsDir: e.target.value })} className={`${field} w-full`} /></label>
      <label>프로젝트 폴더 (서버)<input aria-label="서버 프로젝트 폴더" value={answers.projectsDir} onChange={e => setAnswers({ ...answers, projectsDir: e.target.value })} className={`${field} w-full`} /></label>
      <label>포트<input aria-label="서버 포트" type="number" min={1} max={65535} value={answers.port} onChange={e => setAnswers({ ...answers, port: Number(e.target.value) })} className={`${field} w-full`} /></label>
      <label>관리자 계정 이름<input aria-label="관리자 계정 이름" value={answers.admin} onChange={e => setAnswers({ ...answers, admin: e.target.value })} className={`${field} w-full`} /></label>
      <label className="sm:col-span-2">SSH 접속 (작업자 컴퓨터에서 서버로)<input aria-label="SSH 접속 대상" value={answers.sshTarget} onChange={e => setAnswers({ ...answers, sshTarget: e.target.value })} className={`${field} w-full`} /></label>
    </div>
    <ol className="mt-2 space-y-1.5">{setupCommands(answers).map(step => <li key={step.label}>
      <p>{step.label} · <span className="text-slate-400">{step.where}</span></p>
      <div className="flex gap-1"><code className="flex-1 break-all rounded bg-slate-950 px-2 py-1 text-slate-100">{step.command}</code>
        <button type="button" onClick={() => void copy(step.command)} className="rounded border border-slate-600 px-2">{copied === step.command ? '복사됨' : '복사'}</button></div>
    </li>)}</ol>
    <p className="mt-2 text-slate-400">다른 컴퓨터에서 터널 없이 바로 접속하려면 서버 앞에 HTTPS 리버스 프록시(인증서 포함)를 두고 https:// 주소를 쓰세요. 이 앱은 이 컴퓨터가 아닌 http:// 주소로는 로그인하지 않습니다(비밀번호가 암호화되지 않음). 서버를 0.0.0.0으로 열지 마세요.</p>
    <p className="mt-1 text-slate-400">로그인 뒤 관리자는 "프로젝트·계정 관리"에서 프로젝트(작업 종류 선택)와 계정을 만들고 역할(보기·라벨 작업·학습·검사·검토·승인·프로젝트 관리)을 줍니다.</p>
  </details>;
}
