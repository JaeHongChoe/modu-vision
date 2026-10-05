import React, { useEffect, useRef, useState } from 'react';
import { Archive, ArchiveRestore, ArrowRight, Clock3, FolderOpen, FolderPlus, HardDrive, Layers3, X } from 'lucide-react';
import type { VisionTask } from '../../types';
import { useProjectStore } from '../../stores/useProjectStore';
import { host } from '../../services/hostAdapter';
import { ArtifactRetentionPanel } from './ArtifactRetentionPanel';
import { api } from '../../services/api';

type Tab = 'recent' | 'create' | 'open' | 'backup' | 'restore' | 'retention' | 'template';

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
    backupProject, restoreProject,
  } = useProjectStore();
  const [tab, setTab] = useState<Tab>('recent');
  const [name, setName] = useState('');
  const [task, setTask] = useState<VisionTask>('classification');
  const [description, setDescription] = useState('');
  const [createPath, setCreatePath] = useState('');
  const [openPath, setOpenPath] = useState('');
  const [backupFolder, setBackupFolder] = useState('');
  const [archivePath, setArchivePath] = useState('');
  const [restorePath, setRestorePath] = useState('');
  const [templateText, setTemplateText] = useState('');
  const [templateBusy, setTemplateBusy] = useState(false);
  const [templateError, setTemplateError] = useState('');
  const templateGeneration = useRef(0);
  useEffect(() => () => { ++templateGeneration.current; }, []);
  const loadTemplate = async () => {
    const generation = ++templateGeneration.current;
    setTemplateBusy(true); setTemplateError('');
    try {
      const setup = await api.project.template();
      if (generation === templateGeneration.current) setTemplateText(JSON.stringify(setup, null, 2));
    } catch (error) {
      if (generation === templateGeneration.current) setTemplateError(error instanceof Error ? error.message : String(error));
    } finally { if (generation === templateGeneration.current) setTemplateBusy(false); }
  };
  const createFromTemplate = async (event: React.FormEvent) => {
    event.preventDefault(); setTemplateError('');
    try {
      const template = JSON.parse(templateText) as Record<string, unknown>;
      if (!template || typeof template !== 'object' || Array.isArray(template)) throw Error('템플릿 JSON 객체를 입력하세요.');
      if (await createProject({ name: name.trim(), task, project_dir: createPath.trim() || undefined, template })) onClose();
    } catch (error) { setTemplateError(error instanceof Error ? error.message : String(error)); }
  };
  const [backupMessage, setBackupMessage] = useState('');
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

  // A browser has no folder or file dialog: the buttons say so and the paths are typed in.
  const pickPaths = host.can('pickPaths');
  const pickTitle = pickPaths ? undefined : '브라우저에서는 폴더·파일 선택 창을 열 수 없습니다. 경로를 직접 입력하세요.';

  const selectDirectory = async (forCreate: boolean) => {
    if (!pickPaths) return;
    const path = await host.selectFolder({
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

  const submitBackup = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!backupFolder.trim()) return;
    setBackupMessage('');
    const result = await backupProject(backupFolder.trim());
    if (result) setBackupMessage(`백업 완료: ${result.archive_path} · ${result.file_count}개 파일 · 원본 ${(result.source_bytes / 1024 / 1024).toFixed(1)} MB`);
  };

  const submitRestore = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!archivePath.trim() || !restorePath.trim()) return;
    if (await restoreProject(archivePath.trim(), restorePath.trim())) onClose();
  };

  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-[#05080E]/80 px-5 py-8" onMouseDown={(event) => {
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
              ['backup', Archive, '프로젝트 백업'],
              ['restore', ArchiveRestore, '백업에서 복원'],
              ['retention', HardDrive, '보존기한·복구 보관함'],
              ['template', Layers3, '프로젝트 설정 템플릿'],
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

            {tab === 'template' && (
              <form onSubmit={createFromTemplate} className="space-y-4">
                <h3 className="text-sm font-semibold text-slate-100">프로젝트 설정 템플릿</h3>
                <p className="text-xs leading-5 text-slate-400">검사 유형·학습 프리셋·태그 색상·보존 정책을 새 프로젝트에서 재사용합니다. 원본·모델·검사 기록·권한은 포함되지 않습니다. 전체 작업을 이동하려면 프로젝트 백업을 사용하세요.</p>
                <button type="button" disabled={templateBusy || isProjectBusy} onClick={() => void loadTemplate()} className="rounded border border-slate-600 px-3 py-2 text-xs text-slate-200 disabled:opacity-40">현재 설정을 템플릿으로 읽기</button>
                <label className="block text-xs text-slate-300">템플릿 JSON<textarea aria-label="프로젝트 설정 템플릿 JSON" value={templateText} onChange={(event) => { ++templateGeneration.current; setTemplateBusy(false); setTemplateText(event.target.value); }} rows={10} className="mt-1 w-full rounded border border-slate-600 bg-slate-950 p-2 font-mono text-xs text-white" /></label>
                <p className="text-xs text-slate-500">JSON을 복사해 다른 환경에서 붙여 넣을 수 있습니다. 서버에서 형식과 내용 해시를 검증합니다.</p>
                <label className="block text-xs text-slate-300">새 프로젝트 이름<input aria-label="템플릿 새 프로젝트 이름" value={name} maxLength={100} onChange={(event) => setName(event.target.value)} className="mt-1 w-full rounded border border-slate-600 bg-slate-950 p-2 text-white" /></label>
                <label className="block text-xs text-slate-300">새 프로젝트 폴더 · 비우면 기본 위치<input aria-label="템플릿 새 프로젝트 폴더" value={createPath} onChange={(event) => setCreatePath(event.target.value)} className="mt-1 w-full rounded border border-slate-600 bg-slate-950 p-2 text-white" /></label>
                <button type="submit" disabled={!name.trim() || !templateText.trim() || templateBusy || isProjectBusy} className="rounded bg-cyan-600 px-4 py-2 text-sm text-white disabled:opacity-40">템플릿으로 새 프로젝트 만들기</button>
                {templateError && <p role="alert" className="text-xs text-red-300">{templateError}</p>}
              </form>
            )}

            {tab === 'create' && (
              <form onSubmit={submitCreate} className="space-y-4">
                <div><h3 className="text-sm font-semibold text-slate-100">새 프로젝트</h3><p className="mt-1 text-xs text-slate-500">작업 이름과 검사 유형을 정합니다.</p></div>
                <label className="block text-xs font-medium text-slate-300">프로젝트 이름 <span className="text-cyan-300">*</span><input autoFocus value={name} maxLength={100} onChange={(event) => setName(event.target.value)} placeholder="예: 세라믹 표면 결함 검사" className="mt-1.5 w-full rounded-md border border-[#40516A] bg-[#0D1420] px-3 py-2.5 text-sm text-white outline-none placeholder:text-slate-400 focus:border-cyan-500" /></label>
                <label className="block text-xs font-medium text-slate-300">검사 유형<select value={task} onChange={(event) => setTask(event.target.value as VisionTask)} className="mt-1.5 w-full rounded-md border border-[#40516A] bg-[#0D1420] px-3 py-2.5 text-sm text-white outline-none focus:border-cyan-500">{Object.entries(taskLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
                <label className="block text-xs font-medium text-slate-300">설명 <span className="font-normal text-slate-500">선택</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={2} placeholder="검사 대상과 목적을 기록하세요" className="mt-1.5 w-full resize-none rounded-md border border-[#40516A] bg-[#0D1420] px-3 py-2 text-sm text-white outline-none placeholder:text-slate-400 focus:border-cyan-500" /></label>
                <label className="block text-xs font-medium text-slate-300">저장 폴더 <span className="font-normal text-slate-500">비우면 기본 위치에 생성</span><div className="mt-1.5 flex gap-2"><input value={createPath} onChange={(event) => setCreatePath(event.target.value)} placeholder="기본 프로젝트 폴더 사용" className="min-w-0 flex-1 rounded-md border border-[#40516A] bg-[#0D1420] px-3 py-2.5 font-mono text-xs text-white outline-none placeholder:text-slate-400 focus:border-cyan-500" /><button type="button" disabled={!pickPaths} title={pickTitle} onClick={() => void selectDirectory(true)} className="rounded-md border border-[#40516A] px-3 text-xs text-slate-200 hover:bg-[#25354A]">찾기</button></div></label>
                <button type="submit" disabled={!name.trim() || isProjectBusy} className="flex w-full items-center justify-center gap-2 rounded-md bg-cyan-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-cyan-500 disabled:cursor-not-allowed disabled:opacity-40"><FolderPlus className="h-4 w-4" />{isProjectBusy ? '만드는 중...' : '프로젝트 만들기'}</button>
              </form>
            )}

            {tab === 'open' && (
              <form onSubmit={submitOpen} className="space-y-5">
                <div><h3 className="text-sm font-semibold text-slate-100">기존 프로젝트 열기</h3><p className="mt-1 text-xs leading-5 text-slate-500">project.json이 있는 폴더를 선택하세요. 이미지 데이터 폴더는 데이터 관리 단계에서 엽니다.</p></div>
                <label className="block text-xs font-medium text-slate-300">프로젝트 폴더<div className="mt-1.5 flex gap-2"><input value={openPath} onChange={(event) => setOpenPath(event.target.value)} placeholder="/path/to/project" className="min-w-0 flex-1 rounded-md border border-[#40516A] bg-[#0D1420] px-3 py-2.5 font-mono text-xs text-white outline-none placeholder:text-slate-400 focus:border-cyan-500" /><button type="button" disabled={!pickPaths} title={pickTitle} onClick={() => void selectDirectory(false)} className="rounded-md border border-[#40516A] px-3 text-xs text-slate-200 hover:bg-[#25354A]">찾기</button></div></label>
                <div className="flex items-start gap-2 rounded-md border border-[#32465B] bg-[#152232] p-3 text-xs leading-5 text-slate-400"><HardDrive className="mt-0.5 h-4 w-4 shrink-0 text-cyan-400" /><span>프로젝트 폴더를 열면 저장된 작업 유형과 연결한 원본 데이터 경로를 불러옵니다.</span></div>
                <button type="submit" disabled={!openPath.trim() || isProjectBusy} className="flex w-full items-center justify-center gap-2 rounded-md bg-cyan-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-cyan-500 disabled:cursor-not-allowed disabled:opacity-40"><FolderOpen className="h-4 w-4" />{isProjectBusy ? '여는 중...' : '프로젝트 열기'}</button>
              </form>
            )}

            {tab === 'backup' && (
              <form onSubmit={submitBackup} className="space-y-4">
                <div><h3 className="text-sm font-semibold text-slate-100">프로젝트 전체 백업</h3><p className="mt-1 text-xs leading-5 text-slate-400">현재 프로젝트의 라벨셋·분할·버전·모델·검사 이력과 연결된 원본 이미지 파일을 한 파일에 담습니다. 파일별 SHA-256을 기록하며 원본은 변경하지 않습니다.</p></div>
                <label className="block text-xs font-medium text-slate-300">백업 파일 저장 폴더<div className="mt-1.5 flex gap-2"><input value={backupFolder} onChange={(event) => setBackupFolder(event.target.value)} placeholder="백업 ZIP을 저장할 폴더" className="min-w-0 flex-1 rounded-md border border-[#40516A] bg-[#0D1420] px-3 py-2.5 font-mono text-xs text-white outline-none focus:border-cyan-500" /><button type="button" disabled={!pickPaths} title={pickTitle} onClick={async () => { const path = await host.selectFolder({ title: '프로젝트 백업 저장 폴더' }); if (path) setBackupFolder(path); }} className="rounded-md border border-[#40516A] px-3 text-xs text-slate-200 hover:bg-[#25354A]">찾기</button></div></label>
                <div className="rounded-md border border-[#33465B] bg-[#142236] p-3 text-[11px] leading-5 text-slate-400">원본 데이터는 최대 2 GB, 전체 백업은 최대 4 GB입니다. 프로젝트와 원본 데이터 폴더 바깥을 저장 위치로 선택하세요. 백업 중에는 파일이 바뀌지 않아야 합니다.</div>
                <button type="submit" disabled={!backupFolder.trim() || isProjectBusy} className="flex w-full items-center justify-center gap-2 rounded-md bg-cyan-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-cyan-500 disabled:opacity-40"><Archive className="h-4 w-4" />{isProjectBusy ? '백업 파일 검사·작성 중...' : '프로젝트 백업 만들기'}</button>
                {backupMessage && <p role="status" className="break-all rounded-md border border-emerald-700/50 bg-emerald-950/20 p-3 text-xs leading-5 text-emerald-200">{backupMessage}</p>}
              </form>
            )}

            {tab === 'retention' && project && <ArtifactRetentionPanel projectId={project.id} />}

            {tab === 'restore' && (
              <form onSubmit={submitRestore} className="space-y-4">
                <div><h3 className="text-sm font-semibold text-slate-100">백업에서 새 프로젝트 복원</h3><p className="mt-1 text-xs leading-5 text-slate-400">백업의 모든 파일을 검증한 후 새 폴더에 복원합니다. 원본 프로젝트와 기존 이미지 폴더는 덮어쓰지 않습니다.</p></div>
                <label className="block text-xs font-medium text-slate-300">백업 파일<div className="mt-1.5 flex gap-2"><input value={archivePath} onChange={(event) => setArchivePath(event.target.value)} placeholder=".mvision.zip 파일 경로" className="min-w-0 flex-1 rounded-md border border-[#40516A] bg-[#0D1420] px-3 py-2.5 font-mono text-xs text-white outline-none focus:border-cyan-500" /><button type="button" disabled={!pickPaths} title={pickTitle} onClick={async () => { const path = await host.selectFile({ title: '프로젝트 백업 파일', filters: [{ name: 'Modu Vision 백업', extensions: ['zip'] }] }); if (path) setArchivePath(path); }} className="rounded-md border border-[#40516A] px-3 text-xs text-slate-200 hover:bg-[#25354A]">찾기</button></div></label>
                <label className="block text-xs font-medium text-slate-300">새 프로젝트 폴더<div className="mt-1.5 flex gap-2"><input value={restorePath} onChange={(event) => setRestorePath(event.target.value)} placeholder="아직 존재하지 않는 새 폴더 경로" className="min-w-0 flex-1 rounded-md border border-[#40516A] bg-[#0D1420] px-3 py-2.5 font-mono text-xs text-white outline-none focus:border-cyan-500" /><button type="button" disabled={!pickPaths} title={pickTitle} onClick={async () => { const path = await host.selectFolder({ title: '복원할 새 프로젝트의 상위 폴더' }); if (path) setRestorePath(`${path.replace(/[\\/]+$/, '')}/restored-project`); }} className="rounded-md border border-[#40516A] px-3 text-xs text-slate-200 hover:bg-[#25354A]">상위 폴더</button></div></label>
                <div className="rounded-md border border-amber-700/40 bg-amber-950/20 p-3 text-[11px] leading-5 text-amber-200">복원된 원본 이미지는 새 프로젝트의 <span className="font-mono">dataset/restored_source</span>에 연결됩니다. 예전 체크포인트는 경로가 바뀌므로 출처 검증 후 사용하고, 검증 실패 시 재학습하세요.</div>
                <button type="submit" disabled={!archivePath.trim() || !restorePath.trim() || isProjectBusy} className="flex w-full items-center justify-center gap-2 rounded-md bg-cyan-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-cyan-500 disabled:opacity-40"><ArchiveRestore className="h-4 w-4" />{isProjectBusy ? '파일 검증·복원 중...' : '새 프로젝트로 복원'}</button>
              </form>
            )}

            {projectError && <div role="alert" className="mt-5 rounded-md border border-red-700/60 bg-red-950/30 px-3 py-2 text-xs leading-5 text-red-200">{projectError}</div>}
          </div>
        </div>
      </div>
    </div>
  );
};
