import { useEffect, useState } from 'react';
import { host } from '../../services/hostAdapter';
import { currentFirstUseStudyScope, subscribeFirstUseStudyScope, useFirstUseStudyStore } from './firstUseStudyStore';
import type { StudyMode, StudyTask } from './firstUseStudy';

const TASKS: Record<StudyTask, string> = { first_flow: '첫 프로젝트 → 검사 흐름 저장', manual_label: '직접 라벨 달기', assisted_draft: '초안 검토·수정·채택' };
const duration = (ms: number) => `${Math.floor(ms / 60000)}분 ${Math.floor(ms / 1000) % 60}초`;

/** App owns this observer, so closing the guide and changing stages do not stop observation. */
export function FirstUseStudyObserver() {
  useEffect(() => {
    const sync = () => useFirstUseStudyStore.getState().checkScope(currentFirstUseStudyScope());
    const count = (kind: 'pointer' | 'keyboard', event: Event) => {
      if (useFirstUseStudyStore.getState().snapshot?.status !== 'recording') return;
      sync();
      // No key/code, input value, text, label, coordinate or event object is copied or logged.
      const target = event.target as Element | null;
      const own = typeof target?.closest === 'function' && Boolean(target.closest('[data-first-use-study-controls]'));
      useFirstUseStudyStore.getState().count(kind, own);
    };
    const pointer = (event: Event) => count('pointer', event);
    const keyboard = (event: Event) => count('keyboard', event);
    const pagehide = () => useFirstUseStudyStore.getState().abort('page_unloaded');
    window.addEventListener('pointerdown', pointer, true);
    window.addEventListener('keydown', keyboard, true);
    window.addEventListener('pagehide', pagehide);
    const unsubscribe = subscribeFirstUseStudyScope(sync);
    const timer = setInterval(() => useFirstUseStudyStore.getState().tick(), 1000);
    return () => {
      window.removeEventListener('pointerdown', pointer, true);
      window.removeEventListener('keydown', keyboard, true);
      window.removeEventListener('pagehide', pagehide);
      unsubscribe(); clearInterval(timer);
      useFirstUseStudyStore.getState().abort('observer_removed');
    };
  }, []);
  return null;
}

export function FirstUseStudyPanel() {
  const state = useFirstUseStudyStore();
  const [mode, setMode] = useState<StudyMode>('human_self_reported');
  const [task, setTask] = useState<StudyTask>('first_flow');
  const [build, setBuild] = useState<unknown>({ platform: host.kind() === 'browser' ? 'browser' : 'unknown' });
  const [external, setExternal] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => {
    let current = true;
    // Existing local version hook only; status() does not check updates or call a provider.
    if (host.kind() === 'desktop') void host.updates.status().then(value => {
      if (current) setBuild({ app_version: value.app_version, platform: value.platform, architecture: value.architecture });
    }).catch(() => {});
    return () => { current = false; };
  }, []);
  const value = state.snapshot, running = value?.status === 'recording';
  const bind = () => setNotice(state.bind(currentFirstUseStudyScope()) ? '현재 프로젝트·폴더로 고정했습니다.' : '프로젝트를 만들고 이미지 폴더를 불러온 뒤 현재 작업으로 고정하세요.');
  const start = () => {
    if (state.start({ mode, task, build })) { setExternal(''); setNotice('측정을 시작했습니다. 프로젝트·폴더 준비가 끝나면 현재 작업으로 한 번 고정하세요.'); }
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(state.exportReport(), null, 2)], { type: 'application/json;charset=utf-8' }));
    try { const link = document.createElement('a'); link.href = url; link.download = 'modu-first-use-study.json'; link.click(); }
    finally { URL.revokeObjectURL(url); }
  };
  const reportExternal = () => {
    if (!/^\d+$/.test(external) || !state.reportExternalActions(Number(external))) setNotice('외부 창 조작 횟수는 0 이상의 정수로 입력하세요.');
    else setNotice('외부 창 조작 횟수를 자기 보고로 기록했습니다. 관찰 누락이 없다는 증명은 아닙니다.');
  };
  return <details className="rounded border border-slate-600 p-3" data-first-use-study-controls>
    <summary className="cursor-pointer font-semibold text-slate-100">시간·조작 수 직접 기록 (선택){running ? ' · 기록 중' : ''}</summary>
    <section aria-label="처음 사용자 로컬 측정" className="mt-3 space-y-3">
    <h3 className="font-semibold text-slate-100">처음 사용자 로컬 측정 (선택)</h3>
    <p className="text-slate-300">시작 전에는 기록하지 않습니다. 누른 횟수와 키 입력 횟수만 세고 입력 내용은 읽지 않습니다. 기록은 메모리에만 남으며 앱을 다시 열면 사라집니다. JSON 저장은 직접 누를 때만 합니다.</p>
    <p className="text-amber-200">실제 사람 여부는 자기 보고입니다. 자동 조작 결과는 처음 사용자 시험이 아니며, 모든 결과는 미검증입니다. 30분·40회 목표 통과나 품질 승인을 자동으로 판정하지 않습니다.</p>
    <div className="flex flex-wrap items-end gap-2">
      <label>측정 방식<select aria-label="측정 방식" value={mode} disabled={running} onChange={event => setMode(event.target.value as StudyMode)} className="ml-2 rounded border border-slate-600 bg-slate-900 p-1.5"><option value="human_self_reported">실제 사람 (자기 보고)</option><option value="automated">자동 조작</option></select></label>
      <label>과제<select aria-label="측정 과제" value={task} disabled={running} onChange={event => setTask(event.target.value as StudyTask)} className="ml-2 rounded border border-slate-600 bg-slate-900 p-1.5">{Object.entries(TASKS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
      <button type="button" className="workspace-button" disabled={running} onClick={start}>로컬 측정 시작</button>
      <button type="button" className="workspace-button" disabled={!running || value?.binding === 'bound'} onClick={bind}>현재 프로젝트·폴더로 고정</button>
    </div>
    <p className="text-slate-400">프로젝트 만들기·첫 폴더 선택 전부터 시간을 잽니다. 준비 후 한 번 고정하면 프로젝트·폴더·작업 종류·라벨셋·연결이 바뀔 때 부분 기록으로 중단합니다. 창을 닫아도 계속 기록하고, 대기·수정·백그라운드 시간을 빼지 않습니다.</p>
    {value && <div role="status" aria-label="로컬 측정 상태" className="space-y-1 rounded border border-slate-700 p-2">
      <p>{TASKS[value.task]} · {value.mode === 'automated' ? '자동 조작' : '실제 사람 (자기 보고)'} · {value.status === 'recording' ? '기록 중' : value.status === 'aborted' ? '범위 변경·관찰 종료로 중단한 부분 기록' : '기록 종료'} · {duration(value.elapsed_ms)} · {value.binding === 'bound' ? '작업 고정됨' : '준비 중·아직 고정 안 함'}</p>
      <p>작업 포인터 {value.workflow_actions.pointer}회 · 키 입력 {value.workflow_actions.keyboard}회 · 측정 도구 포인터 {value.recorder_actions.pointer}회 · 도구 키 입력 {value.recorder_actions.keyboard}회</p>
      <p>외부 창 조작 {value.external_actions.count === null ? '미관찰·횟수 모름' : `${value.external_actions.count}회 (자기 보고)`} · 통과 판정 미검증</p>
      {value.images_completed_self_reported > 0 && <p>이미지 완료 {value.images_completed_self_reported}장 (자기 보고) · 전체 경과/장 {duration(value.elapsed_ms_per_image || 0)}</p>}
      {value.clock_anomaly && <p className="text-amber-200">컴퓨터 시각이 바뀌어 시간 비교에 추가 확인이 필요합니다.</p>}
    </div>}
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" className="workspace-button" disabled={!running || value?.task === 'first_flow'} onClick={() => state.mark('image_complete_self_reported')}>이미지 1장 완료로 기록</button>
      <button type="button" className="workspace-button" disabled={!running} onClick={() => state.mark('help_self_reported')}>도움 요청으로 기록</button>
      <button type="button" className="workspace-button" disabled={!running} onClick={state.stop}>기록 끝내기</button>
      <button type="button" className="workspace-button" disabled={!value} onClick={download}>측정 JSON 저장</button>
    </div>
    <p className="text-slate-400">직접 라벨링과 초안 검토는 같은 이미지·난이도인지 자동 확인하지 않습니다. 장당 시간은 완료 표시 뒤의 수정과 대기까지 포함한 전체 경과를 완료 장수로 나눈 관찰값입니다.</p>
    <div className="flex flex-wrap items-center gap-2"><label>시스템 폴더 창·외부 브라우저 조작 합계 (선택)<input aria-label="외부 창 조작 횟수" type="number" min={0} step={1} value={external} disabled={!running} onChange={event => { if (/^\d{0,15}$/.test(event.target.value)) setExternal(event.target.value); }} className="ml-2 w-24 rounded border border-slate-600 bg-slate-900 p-1.5" /></label><button type="button" className="workspace-button" disabled={!running || external === ''} onClick={reportExternal}>외부 창 횟수 자기 보고</button></div>
    <p className="text-slate-400">앱 밖의 조작은 자동으로 셀 수 없습니다. 비워 두면 0회가 아니라 모름으로 남깁니다. 안내 창 닫기·다시 열기는 작업 조작, 이 측정 영역의 조작은 측정 도구 횟수로 따로 셉니다. 종료된 기록은 최근 20개까지 메모리에 보관합니다.</p>
    {state.history.length > 0 && <ul aria-label="종료된 로컬 측정" className="space-y-1 text-slate-400">{state.history.map((row, index) => <li key={index}>{TASKS[row.task]} · {row.mode === 'automated' ? '자동 조작' : '사람 자기 보고'} · {duration(row.elapsed_ms)} · 완료 {row.images_completed_self_reported}장 · 미검증</li>)}</ul>}
    {notice && <p role="status" className="text-cyan-200">{notice}</p>}
    </section>
  </details>;
}
