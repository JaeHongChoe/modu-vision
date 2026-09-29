import React, { useEffect, useRef, useState } from 'react';
import { ArrowRight, Clock3, FolderOpen, FolderPlus, HardDrive, Layers3, X } from 'lucide-react';
import type { VisionTask } from '../../types';
import { useProjectStore } from '../../stores/useProjectStore';

type Tab = 'recent' | 'create' | 'open';

const taskLabels: Record<VisionTask, string> = {
  classification: '분류',
  detection: '객체 검출',
  segmentation: '영역 분할',
  anomaly: '이상 탐지',
};

interface Props {
  onClose: () => void;
}

export const ProjectWorkspaceDialog: React.FC<Props> = ({ onClose }) => {
  const {
    project, recentProjects, isProjectBusy, projectError, loadRecentProjects,
    clearProjectError, createProject, openProject,
  } = useProjectStore();
  const [tab, setTab] = useState<Tab>('recent');
  const [name, setName] = useState('');
  const [task, setTask] = useState<VisionTask>('classification');
  const [description, setDescription] = useState('');
  const [createPath, setCreatePath] = useState('');
  const [openPath, setOpenPath] = useState('');
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void loadRecentProjects();
    dialogRef.current?.focus();
  }, [loadRecentProjects]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !isProjectBusy) onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isProjectBusy, onClose]);

  const selectDirectory = async (forCreate: boolean) => {
    if (!window.api?.selectFolder) return;
    const path = await window.api.selectFolder({
      title: forCreate ? '새 프로젝트를 저장할 폴더' : 'project.json이 있는 프로젝트 폴더',
      defaultPath: project?.project_dir,
    });
    if (path) (forCreate ? setCreatePath : setOpenPath)(path);
  };

  const submitCreate = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    const created = await createProject({
      name: name.trim(), task, description: description.trim(),
      ...(createPath.trim() ? { project_dir: createPath.trim() } : {}),
    });
    if (created) onClose();
  };

  const submitOpen = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!openPath.trim()) return;
    if (await openProject(openPath.trim())) onClose();
  };

  const switchTab = (next: Tab) => {
    clearProjectError();
    setTab(next);
  };

  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-[#05080E]/80 px-5 py-8 backdrop-blur-[3px]" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !isProjectBusy) onClose();
    }}>
      <div ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label="프로젝트 관리" className="flex max-h-full w-full max-w-[830px] flex-col overflow-hidden rounded-xl border border-[#314158] bg-[#111927] shadow-[0_32px_100px_rgba(0,0,0,0.55)] outline-none">
        <div className="flex items-start justify-between border-b border-[#314158] bg-[#172236] px-7 py-6">
          <div>
            <div className="mb-2 flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.2em] text-cyan-300">
              <Layers3 className="h-3.5 w-3.5" /> WORKSPACE
            </div>
            <h2 className="text-xl font-semibold tracking-tight text-white">프로젝트 관리</h2>
            <p className="mt-1.5 text-xs leading-5 text-slate-400">검사 작업별 데이터, 라벨, 모델 설정을 다시 열 수 있습니다.</p>
          </div>
          <button type="button" onClick={onClose} disabled={isProjectBusy} aria-label="프로젝트 관리 닫기" className="rounded-md border border-[#3A4A60] p-1.5 text-slate-300 hover:bg-[#27364A] disabled:opacity-50"><X className="h-4 w-4" /></button>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-[220px_minmax(0,1fr)]">
          <aside className="border-r border-[#314158] bg-[#101722] p-4">
            <div className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">현재 작업</div>
            <div className="mb-5 rounded-lg border border-cyan-700/40 bg-cyan-950/20 p-3">
              <div className="truncate text-sm font-semibold text-white" title={project?.name}>{project?.name || '연결 중'}</div>
              <div className="mt-1.5 flex items-center gap-1.5 text-[11px] text-cyan-300"><span className="h-1.5 w-1.5 rounded-full bg-cyan-400" />{project ? taskLabels[project.task] : '대기'}</div>
              <div className="mt-3 break-all font-mono text-[10px] leading-4 text-slate-500">{project?.project_dir || '—'}</div>
            </div>
            {([
              ['recent', Clock3, '최근 프로젝트'],
              ['create', FolderPlus, '새 프로젝트'],
              ['open', FolderOpen, '폴더에서 열기'],
            ] as const).map(([id, Icon, label]) => (
              <button key={id} type="button" onClick={() => switchTab(id)} className={`mb-1 flex w-full items-center gap-2.5 rounded-md px-3 py-2.5 text-left text-xs font-medium transition-colors ${tab === id ? 'bg-[#263D58] text-cyan-200' : 'text-slate-400 hover:bg-[#1C293A] hover:text-slate-200'}`}>
                <Icon className="h-4 w-4" />{label}
              </button>
            ))}
          </aside>

          <div className="min-h-0 overflow-y-auto p-7">
            {tab === 'recent' && (
              <section>
                <div className="mb-4 flex items-baseline justify-between">
                  <div><h3 className="text-sm font-semibold text-slate-100">최근 프로젝트</h3><p className="mt-1 text-xs text-slate-500">이름을 선택하면 이전 작업 공간으로 이동합니다.</p></div>
                  <span className="font-mono text-[10px] text-slate-500">{recentProjects.length} RECENT</span>
                </div>
                <div className="space-y-2">
                  {recentProjects.length === 0 && <div className="rounded-lg border border-dashed border-[#39485D] p-8 text-center text-xs text-slate-400">아직 저장된 프로젝트가 없습니다. 새 프로젝트를 만들어 시작하세요.</div>}
                  {recentProjects.map((item) => {
                    const active = item.project_dir === project?.project_dir;
                    return <button key={item.project_dir} type="button" disabled={active || isProjectBusy} onClick={async () => { if (await openProject(item.project_dir)) onClose(); }} className="group flex w-full items-center gap-3 rounded-lg border border-[#324057] bg-[#172234] p-3 text-left hover:border-cyan-600 hover:bg-[#1B2B3E] disabled:cursor-default disabled:opacity-65">
                      <span className="rounded-md border border-[#3A516B] bg-[#20334A] p-2 text-cyan-300"><FolderOpen className="h-4 w-4" /></span>
                      <span className="min-w-0 flex-1"><span className="flex items-center gap-2 text-xs font-semibold text-slate-100">{item.name}{active && <span className="rounded bg-cyan-900/40 px-1.5 py-0.5 text-[9px] text-cyan-300">현재</span>}</span><span className="mt-1 block truncate font-mono text-[10px] text-slate-500" title={item.project_dir}>{item.project_dir}</span></span>
                      <span className="text-[10px] text-slate-500">{taskLabels[item.task]}</span><ArrowRight className="h-3.5 w-3.5 text-slate-500 group-hover:text-cyan-300" />
                    </button>;
                  })}
                </div>
              </section>
            )}

            {tab === 'create' && (
              <form onSubmit={submitCreate} className="space-y-4">
                <div><h3 className="text-sm font-semibold text-slate-100">새 프로젝트</h3><p className="mt-1 text-xs text-slate-500">작업 이름과 검사 유형을 정합니다.</p></div>
                <label className="block text-xs font-medium text-slate-300">프로젝트 이름 <span className="text-cyan-300">*</span><input autoFocus value={name} maxLength={100} onChange={(event) => setName(event.target.value)} placeholder="예: 세라믹 표면 결함 검사" className="mt-1.5 w-full rounded-md border border-[#40516A] bg-[#0D1420] px-3 py-2.5 text-sm text-white outline-none placeholder:text-slate-600 focus:border-cyan-500" /></label>
                <label className="block text-xs font-medium text-slate-300">검사 유형<select value={task} onChange={(event) => setTask(event.target.value as VisionTask)} className="mt-1.5 w-full rounded-md border border-[#40516A] bg-[#0D1420] px-3 py-2.5 text-sm text-white outline-none focus:border-cyan-500">{Object.entries(taskLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
                <label className="block text-xs font-medium text-slate-300">설명 <span className="font-normal text-slate-500">선택</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={2} placeholder="검사 대상과 목적을 기록하세요" className="mt-1.5 w-full resize-none rounded-md border border-[#40516A] bg-[#0D1420] px-3 py-2 text-sm text-white outline-none placeholder:text-slate-600 focus:border-cyan-500" /></label>
                <label className="block text-xs font-medium text-slate-300">저장 폴더 <span className="font-normal text-slate-500">비우면 기본 위치에 생성</span><div className="mt-1.5 flex gap-2"><input value={createPath} onChange={(event) => setCreatePath(event.target.value)} placeholder="기본 프로젝트 폴더 사용" className="min-w-0 flex-1 rounded-md border border-[#40516A] bg-[#0D1420] px-3 py-2.5 font-mono text-xs text-white outline-none placeholder:text-slate-600 focus:border-cyan-500" /><button type="button" onClick={() => void selectDirectory(true)} className="rounded-md border border-[#40516A] px-3 text-xs text-slate-200 hover:bg-[#25354A]">찾기</button></div></label>
                <button type="submit" disabled={!name.trim() || isProjectBusy} className="flex w-full items-center justify-center gap-2 rounded-md bg-cyan-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-cyan-500 disabled:cursor-not-allowed disabled:opacity-40"><FolderPlus className="h-4 w-4" />{isProjectBusy ? '만드는 중...' : '프로젝트 만들기'}</button>
              </form>
            )}

            {tab === 'open' && (
              <form onSubmit={submitOpen} className="space-y-5">
                <div><h3 className="text-sm font-semibold text-slate-100">기존 프로젝트 열기</h3><p className="mt-1 text-xs leading-5 text-slate-500">project.json이 있는 폴더를 선택하세요. 이미지 데이터 폴더는 데이터 관리 단계에서 엽니다.</p></div>
                <label className="block text-xs font-medium text-slate-300">프로젝트 폴더<div className="mt-1.5 flex gap-2"><input value={openPath} onChange={(event) => setOpenPath(event.target.value)} placeholder="/path/to/project" className="min-w-0 flex-1 rounded-md border border-[#40516A] bg-[#0D1420] px-3 py-2.5 font-mono text-xs text-white outline-none placeholder:text-slate-600 focus:border-cyan-500" /><button type="button" onClick={() => void selectDirectory(false)} className="rounded-md border border-[#40516A] px-3 text-xs text-slate-200 hover:bg-[#25354A]">찾기</button></div></label>
                <div className="flex items-start gap-2 rounded-md border border-[#32465B] bg-[#152232] p-3 text-xs leading-5 text-slate-400"><HardDrive className="mt-0.5 h-4 w-4 shrink-0 text-cyan-400" /><span>프로젝트 폴더를 열면 저장된 작업 유형과 연결한 원본 데이터 경로를 불러옵니다.</span></div>
                <button type="submit" disabled={!openPath.trim() || isProjectBusy} className="flex w-full items-center justify-center gap-2 rounded-md bg-cyan-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-cyan-500 disabled:cursor-not-allowed disabled:opacity-40"><FolderOpen className="h-4 w-4" />{isProjectBusy ? '여는 중...' : '프로젝트 열기'}</button>
              </form>
            )}

            {projectError && <div role="alert" className="mt-5 rounded-md border border-red-700/60 bg-red-950/30 px-3 py-2 text-xs leading-5 text-red-200">{projectError}</div>}
          </div>
        </div>
      </div>
    </div>
  );
};
