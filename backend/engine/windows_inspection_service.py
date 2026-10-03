"""SCM entry point, distinct from Studio's user logon task.

Source entry: python -m backend.engine.windows_inspection_service --project-dir ...
Frozen entry must dispatch --windows-inspection-service before starting Studio.
The service executable connects to SCM and supervises one retained child handle.
No service installation or privilege changes happen on import or startup.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import locale
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time

import httpx
from backend.engine.process_isolation import session_isolation


STATES = {'stopped':1,'start_pending':2,'stop_pending':3,'running':4}


def scm_launch_command(project_dir, service_name):
    arguments = ['--project-dir',str(Path(project_dir).resolve()),'--service-name',service_name]
    return ([sys.executable,'--windows-inspection-service'] if getattr(sys,'frozen',False)
            else [sys.executable,'-m','backend.engine.windows_inspection_service']) + arguments


class ScmServiceHost:
    """ServiceMain owns initialization/cleanup; controls only signal an event."""
    def __init__(self,name,scm,spawn,readiness,shutdown,*,startup_timeout=90,
                 stop_timeout=25,poll_seconds=.1):
        self.name,self.scm,self.spawn,self.readiness,self.shutdown=name,scm,spawn,readiness,shutdown
        self.startup_timeout,self.stop_timeout,self.poll_seconds=startup_timeout,stop_timeout,poll_seconds
        self.stop_requested=threading.Event()
        self.status_handle=None
        self.status_lock=threading.RLock()
        self.status=None

    def report(self,state,*,checkpoint=0,win32_exit_code=0):
        with self.status_lock:
            self.status={'state':state,'controls_accepted':5 if state=='running' else 0,
                         'checkpoint':checkpoint,'wait_hint_ms':int((self.startup_timeout if state=='start_pending' else self.stop_timeout)*1000) if state.endswith('pending') else 0,
                         'win32_exit_code':win32_exit_code}
            self.scm.report(self.status_handle,self.status)

    def control_handler(self,control,*_args):
        if control in (1,5):  # STOP and SHUTDOWN
            self.stop_requested.set()
            return 0
        if control==4:  # INTERROGATE: re-publish current state, never repeat STOPPED.
            with self.status_lock:
                if self.status_handle and self.status and self.status['state']!='stopped':
                    self.scm.report(self.status_handle,self.status)
            return 0
        return 120

    def service_main(self):
        self.status_handle=self.scm.register(self.name,self.control_handler)
        if not self.status_handle: return
        child=None
        exit_code=0
        self.report('start_pending',checkpoint=1)
        try:
            child=self.spawn()
            # The checkpoint marks actual process creation, not a timer heartbeat.
            self.report('start_pending',checkpoint=2)
            deadline=time.monotonic()+self.startup_timeout
            while not self.stop_requested.is_set():
                if child.poll() is not None: raise RuntimeError('Inspection child exited before readiness')
                if self.readiness(): break
                if time.monotonic()>=deadline: raise TimeoutError('Inspection readiness timed out')
                self.stop_requested.wait(self.poll_seconds)
            if not self.stop_requested.is_set():
                self.report('running')
                while not self.stop_requested.wait(self.poll_seconds):
                    if child.poll() is not None: raise RuntimeError('Inspection child exited unexpectedly')
        except Exception:
            exit_code=1066  # ERROR_SERVICE_SPECIFIC_ERROR, native adapter supplies detail.
        finally:
            self.report('stop_pending',checkpoint=1)
            try:
                if child is not None:
                    try: self.shutdown()
                    except Exception: pass
                    deadline=time.monotonic()+self.stop_timeout
                    while child.poll() is None and time.monotonic()<deadline:
                        time.sleep(self.poll_seconds)
                    if child.poll() is None:
                        child.terminate_owned()
                        exit_code=exit_code or 1066
            except Exception:
                exit_code=1066
            finally:
                if child is not None: child.close()
                self.report('stopped',win32_exit_code=exit_code)


class Win32SCM:
    def __init__(self):
        if os.name!='nt': raise OSError('SCM dispatcher requires Windows')
        self.api=ctypes.WinDLL('advapi32',use_last_error=True)
        self.main_type=ctypes.WINFUNCTYPE(None,wintypes.DWORD,ctypes.POINTER(wintypes.LPWSTR))
        self.handler_type=ctypes.WINFUNCTYPE(wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,ctypes.c_void_p)
        class Status(ctypes.Structure):
            _fields_=[(name,wintypes.DWORD) for name in ('service_type','state','controls','exit_code','specific_exit','checkpoint','wait_hint')]
        class Entry(ctypes.Structure):
            _fields_=[('name',wintypes.LPWSTR),('main',self.main_type)]
        self.Status,self.Entry=Status,Entry
        self.api.RegisterServiceCtrlHandlerExW.argtypes=[wintypes.LPCWSTR,self.handler_type,ctypes.c_void_p]
        self.api.RegisterServiceCtrlHandlerExW.restype=wintypes.HANDLE
        self.api.SetServiceStatus.argtypes=[wintypes.HANDLE,ctypes.POINTER(Status)]
        self.api.SetServiceStatus.restype=wintypes.BOOL
        self.api.StartServiceCtrlDispatcherW.argtypes=[ctypes.POINTER(Entry)]
        self.api.StartServiceCtrlDispatcherW.restype=wintypes.BOOL
        self.callbacks=[]

    def register(self,name,handler):
        callback=self.handler_type(handler)
        self.callbacks.append(callback)
        handle=self.api.RegisterServiceCtrlHandlerExW(name,callback,None)
        if not handle: raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def report(self,handle,status):
        value=self.Status(0x10,STATES[status['state']],status['controls_accepted'],status['win32_exit_code'],
                          1 if status['win32_exit_code']==1066 else 0,status['checkpoint'],status['wait_hint_ms'])
        if not self.api.SetServiceStatus(handle,ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())

    def dispatch(self,name,main):
        callback=self.main_type(lambda _argc,_argv:main())
        self.callbacks.append(callback)
        table=(self.Entry*2)()
        table[0].name=name
        table[0].main=callback
        if not self.api.StartServiceCtrlDispatcherW(table):
            raise ctypes.WinError(ctypes.get_last_error())


class OwnedWindowsChild:
    """Popen's retained process HANDLE plus kill-on-close job owns descendants."""
    def __init__(self,command,*,cwd,env,log_path):
        if os.name!='nt': raise OSError('Owned SCM child requires Windows')
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        class Basic(ctypes.Structure):
            _fields_=[('per_process_time',ctypes.c_int64),('per_job_time',ctypes.c_int64),('flags',wintypes.DWORD),
                      ('min_working_set',ctypes.c_size_t),('max_working_set',ctypes.c_size_t),('active_process_limit',wintypes.DWORD),
                      ('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[(name,ctypes.c_uint64) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Extended(ctypes.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
                      ('peak_process_memory',ctypes.c_size_t),('peak_job_memory',ctypes.c_size_t)]
        self.api.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype=wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        self.api.SetInformationJobObject.restype=wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype=wintypes.BOOL
        self.api.TerminateJobObject.argtypes=[wintypes.HANDLE,wintypes.UINT]
        self.api.TerminateJobObject.restype=wintypes.BOOL
        self.api.CloseHandle.argtypes=[wintypes.HANDLE]
        self.api.CloseHandle.restype=wintypes.BOOL
        self.api.CreateEventW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.BOOL,wintypes.LPCWSTR]
        self.api.CreateEventW.restype=wintypes.HANDLE
        self.api.SetEvent.argtypes=[wintypes.HANDLE]
        self.api.SetEvent.restype=wintypes.BOOL
        self.job=self.api.CreateJobObjectW(None,None)
        if not self.job: raise ctypes.WinError(ctypes.get_last_error())
        value=Extended()
        value.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        self.process=None
        gate=None
        try:
            if not self.api.SetInformationJobObject(self.job,9,ctypes.byref(value),ctypes.sizeof(value)):
                raise ctypes.WinError(ctypes.get_last_error())
            if Path(log_path).is_symlink(): raise ValueError('Service log is linked')
            gate=self.api.CreateEventW(None,True,False,None)
            if not gate:raise ctypes.WinError(ctypes.get_last_error())
            os.set_handle_inheritable(int(gate),True)
            startupinfo=subprocess.STARTUPINFO()
            startupinfo.lpAttributeList={'handle_list':[int(gate)]}
            child_env=dict(env,VISION_SCM_START_HANDLE=str(int(gate)))
            with Path(log_path).open('ab') as log:
                self.process=subprocess.Popen(command,cwd=cwd,env=child_env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                    startupinfo=startupinfo,close_fds=True,**session_isolation())
            # The child waits on the inherited event before package/device load,
            # closing the process creation -> job assignment descendant race.
            if not self.api.AssignProcessToJobObject(self.job,int(self.process._handle)):
                self.process.kill()
                self.process.wait(timeout=10)
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.api.SetEvent(gate):
                self.terminate_owned()
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise
        finally:
            if gate:self.api.CloseHandle(gate)

    def poll(self): return self.process.poll()
    def terminate_owned(self):
        if not self.api.TerminateJobObject(self.job,1): raise ctypes.WinError(ctypes.get_last_error())
        self.process.wait(timeout=10)
    def close(self):
        if self.job:
            self.api.CloseHandle(self.job)
            self.job=None


def _observe_registration(project_dir,name,configuration):
    """Read-only Windows checks; supplied JSON booleans never grant access."""
    if os.name!='nt': return {}
    import base64
    account=configuration.get('service_account','')
    observed={'elevation':bool(ctypes.windll.shell32.IsUserAnAdmin()),'session0_gpu_camera_readiness':False}
    # Virtual accounts are provisioned by SCM itself. Other accepted accounts are
    # pre-provisioned gMSA accounts; no password is accepted or saved here.
    virtual=account.casefold()==('NT SERVICE\\'+name).casefold()
    gmsa='\\' in account and account.endswith('$')
    observed['service_account']=virtual or gmsa
    root=Path(project_dir).resolve()
    programdata=Path(os.environ.get('ProgramData',r'C:\ProgramData')).resolve()
    observed['programdata_acl']=False
    observed['project_access']=False
    observed['network_credentials']=not configuration.get('network_required',False)
    if not root.is_relative_to(programdata): return observed
    script=r'''
$ErrorActionPreference='Stop'
$root=$env:VISION_SCM_PREFLIGHT_ROOT
$account=$env:VISION_SCM_PREFLIGHT_ACCOUNT
$sid=$env:VISION_SCM_PREFLIGHT_SID
if(-not $sid){$sid=([System.Security.Principal.NTAccount]$account).Translate([System.Security.Principal.SecurityIdentifier]).Value}
$paths=@($root,(Join-Path $root 'runtime_service'))
$safe=$true; $access=$true
foreach($path in $paths){
  $acl=Get-Acl -LiteralPath $path
  if(-not $acl.AreAccessRulesProtected){$safe=$false}
  $allow=$false
  foreach($rule in $acl.Access){
    $who=$rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
    $write=([int]$rule.FileSystemRights -band 0x000D0116) -ne 0
    if($rule.AccessControlType -eq 'Allow' -and $write -and $who -notin @($sid,'S-1-5-18','S-1-5-32-544')){$safe=$false}
    if($who -eq $sid -and $rule.AccessControlType -eq 'Allow' -and (($rule.FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::Modify) -eq [System.Security.AccessControl.FileSystemRights]::Modify)){$allow=$true}
    if($who -eq $sid -and $rule.AccessControlType -eq 'Deny'){$access=$false}
  }
  if(-not $allow){$access=$false}
}
foreach($path in @($env:VISION_SCM_RUNTIME_ROOT,$env:VISION_SCM_EXECUTABLE)){
  $acl=Get-Acl -LiteralPath $path
  foreach($rule in $acl.Access){
    $who=$rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
    if($rule.AccessControlType -eq 'Allow' -and (([int]$rule.FileSystemRights -band 0x000D0116) -ne 0) -and $who -notin @('S-1-5-18','S-1-5-32-544')){$safe=$false}
  }
}
@{programdata_acl=$safe;project_access=$access}|ConvertTo-Json -Compress
'''
    from backend.engine.service_bootstrap import runtime_cwd
    runtime_path=runtime_cwd().resolve()
    protected_roots=[programdata,Path(os.environ.get('ProgramFiles',r'C:\Program Files')).resolve()]
    if not all(any(path.is_relative_to(protected) for protected in protected_roots) for path in (runtime_path,Path(sys.executable).resolve())):
        return observed
    env=dict(os.environ,VISION_SCM_PREFLIGHT_ROOT=str(root),VISION_SCM_PREFLIGHT_ACCOUNT=account,
             VISION_SCM_RUNTIME_ROOT=str(runtime_path),VISION_SCM_EXECUTABLE=str(Path(sys.executable).resolve()))
    if virtual:
        import hashlib,struct
        env['VISION_SCM_PREFLIGHT_SID']='S-1-5-80-'+ '-'.join(str(value) for value in struct.unpack('<5I',hashlib.sha1(name.upper().encode('utf-16le')).digest()))
    else:env['VISION_SCM_PREFLIGHT_SID']=''
    try:
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16le')).decode()],env=env,capture_output=True,text=True,encoding=locale.getpreferredencoding(False),errors='replace',timeout=15)
        if result.returncode==0:
            values=json.loads(result.stdout)
            observed.update({key:values.get(key) is True for key in ('programdata_acl','project_access')})
    except (OSError,ValueError,subprocess.SubprocessError): pass
    # Network access and reboot/Session0 readiness cannot be inferred from this
    # interactive Studio token; they remain pending until verified in service.
    return observed


def registration_preflight(project_dir,name,configuration,*,system=None,observer=None):
    system=system or platform.system()
    actual=(observer or _observe_registration)(project_dir,name,configuration) if system=='Windows' else {}
    requirements={
        'elevation':'An elevated administrator must register SCM; Studio runs without elevation',
        'service_account':'Use this service virtual account or a pre-provisioned gMSA with service logon rights',
        'programdata_acl':'Stage service project/state under ProgramData with protected ACLs for service, SYSTEM and administrators',
        'project_access':'Service account must read approved packages and write its durable state and logs',
        'network_credentials':'Verify UNC/API/PLC access as the service identity without interactive credentials or mapped drives',
        'session0_gpu_camera_readiness':'Verify reboot without user login, GPU warmup and camera/network in Session0',
    }
    account=configuration.get('service_account','')
    dedicated=isinstance(account,str) and (account.casefold()==('NT SERVICE\\'+name).casefold() or ('\\' in account and account.endswith('$')))
    actual['service_account']=dedicated and actual.get('service_account') is True
    checks={key:{'passed':actual.get(key) is True,'requirement':requirement,'evidence':'native_observation' if actual.get(key) is True else 'pending'} for key,requirement in requirements.items()}
    prerequisites=system=='Windows' and all(checks[key]['passed'] for key in requirements if key!='session0_gpu_camera_readiness')
    return {'kind':'windows_scm','platform':system,'startup_scope':'system_boot_session0','studio_requires_admin':False,
            'registration_requires_elevation':True,'service_account':account,'checks':checks,
            'registration_prerequisites_passed':prerequisites,'registerable':prerequisites,
            'native_verified':False,'session0_acceptance':'pending','hardware_acceptance':'pending'}


def main(argv=None):
    parser=argparse.ArgumentParser(description='SCM inspection service executable')
    parser.add_argument('--project-dir',required=True,type=Path)
    parser.add_argument('--service-name',required=True)
    args=parser.parse_args(argv)
    scm=Win32SCM()
    # Connect promptly: expensive imports/bootstrap/package checks run in ServiceMain.
    def spawn():
        from backend.engine.service_bootstrap import bootstrap_command,runtime_cwd
        command,env=bootstrap_command(args.project_dir)
        return OwnedWindowsChild(command,cwd=runtime_cwd(),env=env,
                                 log_path=args.project_dir/'runtime_service'/'service.log')
    def request(path):
        configuration=json.loads((args.project_dir/'runtime_service'/'service.json').read_text(encoding='utf-8'))
        with httpx.Client(base_url=f"http://127.0.0.1:{configuration['port']}",headers={'X-Vision-Token':configuration['token']},timeout=2) as client:
            response=client.post(path) if path.endswith('shutdown') else client.get(path)
            response.raise_for_status()
            return response.json()
    def ready():
        try:
            result=request('/v1/readiness')
            return result.get('status')=='ready'
        except (OSError,ValueError,httpx.HTTPError): return False
    host=ScmServiceHost(args.service_name,scm,spawn,ready,lambda:request('/v1/runtime/shutdown'))
    scm.dispatch(args.service_name,host.service_main)
    return 0


if __name__=='__main__': raise SystemExit(main())
