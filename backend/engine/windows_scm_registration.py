"""Explicit SCM preparation/controls; no implicit elevation or account creation."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import hashlib
import json
import locale
import os
from pathlib import Path
import platform
import subprocess
import time
import uuid

from backend.engine.runtime_process_control import atomic_private_json
from backend.engine.windows_inspection_service import registration_preflight, scm_launch_command


def query_windows_service(name):
    if os.name!='nt': return {'registered':False,'running':False,'platform':'unsupported'}
    api=ctypes.WinDLL('advapi32',use_last_error=True)
    class Config(ctypes.Structure):
        _fields_=[('service_type',wintypes.DWORD),('start_type',wintypes.DWORD),('error_control',wintypes.DWORD),
                  ('binary_path',wintypes.LPWSTR),('group',wintypes.LPWSTR),('tag',wintypes.DWORD),
                  ('dependencies',wintypes.LPWSTR),('account',wintypes.LPWSTR),('display_name',wintypes.LPWSTR)]
    class Status(ctypes.Structure):
        _fields_=[(key,wintypes.DWORD) for key in ('service_type','state','controls','exit_code','specific_exit','checkpoint','wait_hint','pid','flags')]
    api.OpenSCManagerW.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD]
    api.OpenSCManagerW.restype=wintypes.HANDLE
    api.OpenServiceW.argtypes=[wintypes.HANDLE,wintypes.LPCWSTR,wintypes.DWORD]
    api.OpenServiceW.restype=wintypes.HANDLE
    api.QueryServiceConfigW.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
    api.QueryServiceConfigW.restype=wintypes.BOOL
    api.QueryServiceStatusEx.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
    api.QueryServiceStatusEx.restype=wintypes.BOOL
    api.CloseServiceHandle.argtypes=[wintypes.HANDLE]
    api.CloseServiceHandle.restype=wintypes.BOOL
    manager=api.OpenSCManagerW(None,None,1)
    if not manager: raise ctypes.WinError(ctypes.get_last_error())
    handle=None
    try:
        handle=api.OpenServiceW(manager,name,0x0001|0x0004)
        if not handle:
            code=ctypes.get_last_error()
            if code==1060: return {'registered':False,'running':False}
            raise ctypes.WinError(code)
        size=wintypes.DWORD()
        api.QueryServiceConfigW(handle,None,0,ctypes.byref(size))
        if not 0<size.value<=65536: raise OSError('Invalid SCM configuration size')
        buffer=ctypes.create_string_buffer(size.value)
        if not api.QueryServiceConfigW(handle,buffer,size.value,ctypes.byref(size)): raise ctypes.WinError(ctypes.get_last_error())
        config=ctypes.cast(buffer,ctypes.POINTER(Config)).contents
        status=Status()
        if not api.QueryServiceStatusEx(handle,0,ctypes.byref(status),ctypes.sizeof(status),ctypes.byref(size)): raise ctypes.WinError(ctypes.get_last_error())
        return {'registered':True,'running':status.state==4,'enabled':config.start_type==2,
                'pid':int(status.pid) or None,'scm_state':int(status.state),'binary_path':config.binary_path,
                'service_account':config.account,'win32_exit_code':int(status.exit_code)}
    finally:
        if handle: api.CloseServiceHandle(handle)
        api.CloseServiceHandle(manager)


class WindowsScmRegistration:
    def __init__(self,service,*,system=None):
        self.service=service
        self.system=system or platform.system()
        self.label=service.native_identity()
        self.kind='windows_scm'
        self.journal_path=service.root/'scm-install.json'
        self.configuration=service.config.get('scm_configuration',{})

    def registration_path(self): return self.service.root/'install'/(self.label+'.scm.json')

    def _journal(self):
        if not self.journal_path.exists(): return None
        if self.journal_path.is_symlink(): raise ValueError('SCM install journal is linked')
        value=json.loads(self.journal_path.read_text(encoding='utf-8'))
        if value.get('native_label')!=self.label or value.get('platform')!='Windows': raise ValueError('SCM journal ownership differs')
        return value

    def _journal_write(self,status,error=None):
        value={'schema_version':1,'native_label':self.label,'platform':'Windows','operation_id':uuid.uuid4().hex,
               'status':status,'updated_at':time.time(),'error':error}
        atomic_private_json(self.journal_path,value)

    def preflight(self,configuration=None):
        return registration_preflight(self.service.root.parent,self.label,configuration or self.configuration,system=self.system)

    def query(self):
        state={'kind':self.kind,'platform':self.system,'startup_scope':'system_boot_session0','registered':False,
               'running':False,'enabled':False,'verified':False,'native_verified':False,
               'prepared':self.registration_path().is_file(),'supported':self.system=='Windows',
               'session0_acceptance':'pending','hardware_acceptance':'pending'}
        if self.system!='Windows': return state
        observed=query_windows_service(self.label)
        if observed.get('registered'):
            expected=subprocess.list2cmdline(scm_launch_command(self.service.root.parent,self.label))
            if observed.get('binary_path')!=expected or observed.get('service_account','').casefold()!=self.configuration.get('service_account','').casefold():
                raise ValueError('Existing SCM service command or account is not owned by this configuration')
        state.update(observed)
        journal=self._journal()
        if journal: state['install_recovery']=journal
        return state

    def prepare(self,configuration=None):
        configuration=configuration or self.configuration
        if set(configuration)-{'service_account','network_required','warmup_image'}:
            raise ValueError('SCM configuration contains unsupported fields')
        if not configuration.get('service_account'): raise ValueError('SCM requires an explicit dedicated service account')
        self.configuration=dict(configuration)
        self.service.config['scm_configuration']=self.configuration
        self.service.save(self.service.config)
        directory=self.service.root/'install'
        if directory.is_symlink(): raise ValueError('SCM preparation directory is linked')
        directory.mkdir(exist_ok=True)
        descriptor={'schema_version':1,'kind':self.kind,'service_name':self.label,'project_dir':str(self.service.root.parent),
                    'binary_command':scm_launch_command(self.service.root.parent,self.label),'service_account':configuration['service_account'],
                    'startup':'automatic','session':'Session0','studio_requires_admin':False}
        atomic_private_json(self.registration_path(),descriptor)
        return {**self.preflight(),'status':'prepared','files':[str(self.registration_path())],
                'descriptor_sha256':hashlib.sha256(self.registration_path().read_bytes()).hexdigest()}

    @staticmethod
    def _run(arguments):
        result=subprocess.run(arguments,capture_output=True,text=True,encoding=locale.getpreferredencoding(False),errors='replace',timeout=20)
        if result.returncode: raise RuntimeError('SCM command failed: '+result.stderr.strip()[-1000:])
        return result

    def install(self):
        checked=self.preflight()
        if not checked['registration_prerequisites_passed']:
            raise ValueError('SCM registration prerequisites are pending; inspect elevation/account/ProgramData/project/network checks')
        self.prepare()
        state=self.query()
        self._journal_write('registering')
        try:
            if not state['registered']:
                self._run(['sc.exe','create',self.label,'binPath=',subprocess.list2cmdline(scm_launch_command(self.service.root.parent,self.label)),
                           'start=','auto','obj=',self.configuration['service_account']])
            self.service.config.update(native_label=self.label,native_kind=self.kind,native_registration_path=str(self.registration_path()))
            self.service.save(self.service.config)
            if not state.get('running'): self._run(['sc.exe','start',self.label])
            observed=self.query()
            if not observed['registered']: raise RuntimeError('SCM did not confirm service registration')
            self._journal_write('registration_checked')
            return {**observed,'status':'installed','readiness':'Verify /v1/readiness separately'}
        except Exception:
            self._journal_write('registration_failed','Registration/start failed; retain owned service for explicit recovery')
            raise

    def stop(self):
        state=self.query()
        if state.get('running') or state.get('scm_state')==2: self._run(['sc.exe','stop',self.label])
        deadline=time.monotonic()+35
        while state.get('registered') and state.get('scm_state') not in (None,1) and time.monotonic()<deadline:
            time.sleep(.1)
            state=self.query()
        if state.get('registered') and state.get('scm_state') not in (None,1):
            raise TimeoutError('SCM stop is pending; retained child and durable jobs must be checked')
        return state

    def remove(self):
        state=self.stop()
        self._journal_write('removing')
        if state.get('registered'): self._run(['sc.exe','delete',self.label])
        self.registration_path().unlink(missing_ok=True)
        for key in ('native_label','native_kind','native_registration_path'): self.service.config.pop(key,None)
        self.service.save(self.service.config)
        self._journal_write('removed')
        return {**self.query(),'status':'uninstalled'}
