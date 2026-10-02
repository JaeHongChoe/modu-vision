"""Owned user-level startup descriptors and explicit native registration state."""
from __future__ import annotations
import hashlib
import getpass
import locale
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from backend.engine.project_migration import _atomic_bytes
from backend.engine.runtime_process_control import atomic_private_json


class NativeAutostart:
    def __init__(self,service,*,system=None,home=None):
        self.service=service;self.system=system or platform.system();self.home=Path(home or Path.home())
        self.label=service.native_identity()
        self.kind={'Darwin':'launch_agent','Linux':'systemd_user','Windows':'scheduled_task'}.get(self.system,'unsupported')
        self.tool={'Darwin':'launchctl','Linux':'systemctl','Windows':'schtasks.exe'}.get(self.system)
        self.journal_path=service.root/'native-install.json'
        if self.journal_path.is_symlink():raise ValueError('Native install journal cannot be linked')
    def _journal(self):
        if not self.journal_path.exists():return None
        if self.journal_path.is_symlink():raise ValueError('Native install journal cannot be linked')
        value=json.loads(self.journal_path.read_text(encoding='utf-8'))
        if value.get('schema_version')!=1 or value.get('native_label')!=self.label or value.get('platform')!=self.system:
            raise ValueError('Native install journal identity differs from this project')
        return value
    def _write_journal(self,status,*,operation_id=None,error=None):
        from backend.engine.service_bootstrap import trusted_runtime_identity
        active=self.service.ledger.active()
        value={'schema_version':1,'operation_id':operation_id or uuid.uuid4().hex,'status':status,
               'native_label':self.label,'platform':self.system,'registration_path':str(self.registration_path()),
               'descriptor_sha256':hashlib.sha256(self._descriptor()).hexdigest(),
               'manifest_sha256':active['release'].get('manifest_sha256') if active else None,
               'runtime_build':trusted_runtime_identity(),'updated_at':time.time(),'error':error}
        atomic_private_json(self.journal_path,value)
        return value
    def registration_path(self):
        if self.system=='Darwin':return self.home/'Library'/'LaunchAgents'/(self.label+'.plist')
        if self.system=='Linux':return self.home/'.config'/'systemd'/'user'/(self.label+'.service')
        return self.service.root/'install'/(self.label+'.xml')
    def _run(self,args,*,allow_failure=False):
        result=subprocess.run(args,capture_output=True,text=True,encoding=locale.getpreferredencoding(False),errors='replace',timeout=15)  # OS tool messages
        if result.returncode and not allow_failure:raise RuntimeError(f'{self.kind} command failed: '+result.stderr.strip()[-1000:])
        return result
    def _descriptor(self):
        from backend.engine.service_bootstrap import launch_command,runtime_cwd
        arguments=launch_command(self.service.root.parent);cwd=str(runtime_cwd());log=str(self.service.root/'service.log')
        if self.system=='Darwin':
            return plistlib.dumps({'Label':self.label,'ProgramArguments':arguments,'WorkingDirectory':cwd,
                'RunAtLoad':True,'KeepAlive':{'SuccessfulExit':False},'ThrottleInterval':10,
                'StandardOutPath':log,'StandardErrorPath':log})
        if self.system=='Linux':
            def quote(value):return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
            return ('[Unit]\nDescription=Project inspection service\nStartLimitIntervalSec=60\nStartLimitBurst=3\n[Service]\nType=simple\nWorkingDirectory='+quote(cwd)+'\nExecStart='+ ' '.join(quote(a) for a in arguments)+'\nRestart=on-failure\nRestartSec=5\n[Install]\nWantedBy=default.target\n').encode()
        if self.system=='Windows':
            namespace='http://schemas.microsoft.com/windows/2004/02/mit/task';ET.register_namespace('',namespace)
            def element(parent,name,text=None):
                child=ET.SubElement(parent,'{'+namespace+'}'+name)
                if text is not None:child.text=text
                return child
            task=ET.Element('{'+namespace+'}Task',version='1.2')
            triggers=element(task,'Triggers');trigger=element(triggers,'LogonTrigger');element(trigger,'Enabled','true')
            user=(os.environ['USERDOMAIN']+'\\' if os.environ.get('USERDOMAIN') else '')+getpass.getuser()
            element(trigger,'UserId',user)
            principal=element(element(task,'Principals'),'Principal');principal.set('id','Operator');element(principal,'UserId',user);element(principal,'LogonType','InteractiveToken');element(principal,'RunLevel','LeastPrivilege')
            settings=element(task,'Settings');element(settings,'MultipleInstancesPolicy','IgnoreNew');element(settings,'ExecutionTimeLimit','PT0S');element(settings,'StartWhenAvailable','true')
            actions=element(task,'Actions');actions.set('Context','Operator');execute=element(actions,'Exec')
            element(execute,'Command',arguments[0]);element(execute,'Arguments',subprocess.list2cmdline(arguments[1:]));element(execute,'WorkingDirectory',cwd)
            return ET.tostring(task,encoding='utf-8',xml_declaration=True)
        raise ValueError('Native automatic startup is unsupported on this OS')
    def _owned_file(self,path):
        if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('Native registration paths cannot be linked')
        if path.exists() and path.read_bytes()!=self._descriptor():raise ValueError('Existing native registration file is not owned by this configuration')
    def query(self):
        path=self.registration_path();prepared=(self.service.root/'install'/(self.label+{ 'Darwin':'.plist','Linux':'.service'}.get(self.system,'.xml'))).is_file()
        state={'platform':self.system,'kind':self.kind,'supported':self.kind!='unsupported','command_available':bool(self.tool and shutil.which(self.tool)),
               'prepared':prepared,'registered':False,'enabled':False,'running':False,'verified':False,
               'startup_scope':'user_login' if self.system in ('Darwin','Windows') else 'user_session',
               'prerequisite':'A logged-in user session is required; Linux boot without login requires separately configured user lingering'}
        journal=self._journal()
        if journal:
            status={'registering':'interrupted_registration','removing':'interrupted_removal'}.get(journal['status'],journal['status'])
            state['install_recovery']={**journal,'status':status}
        if self.kind=='unsupported':return state
        if not path.exists() and not self.service.config.get('native_kind') and not self.service.config.get('native_label'):return state
        self._owned_file(path)
        try:
            if self.system=='Darwin':
                result=self._run(['launchctl','print',f'gui/{os.getuid()}/{self.label}'],allow_failure=True)
                match=re.search(r'\bpid = (\d+)',result.stdout);state.update(registered=result.returncode==0,enabled=path.is_file(),running=bool(match),pid=int(match[1]) if match else None)
            elif self.system=='Linux':
                result=self._run(['systemctl','--user','show',self.label+'.service','--property=LoadState,ActiveState,MainPID,UnitFileState'],allow_failure=True)
                rows=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
                state.update(registered=rows.get('LoadState')=='loaded',enabled=rows.get('UnitFileState')=='enabled',running=rows.get('ActiveState')=='active',pid=int(rows.get('MainPID','0')) or None)
            else:
                result=self._run(['schtasks.exe','/Query','/TN',self.label,'/XML'],allow_failure=True)
                enabled=False
                if result.returncode==0:
                    document=ET.fromstring(result.stdout)
                    value=document.find('.//{http://schemas.microsoft.com/windows/2004/02/mit/task}Settings/{http://schemas.microsoft.com/windows/2004/02/mit/task}Enabled')
                    enabled=value is None or value.text!='false'
                state.update(registered=result.returncode==0,enabled=enabled,running=None,process_observation='Use the independently verified runtime identity')
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError,ET.ParseError) as exc:state['error']=str(exc)
        return state
    def prepare(self):
        directory=self.service.root/'install'
        if directory.is_symlink():raise ValueError('Native installation storage cannot be linked')
        directory.mkdir(exist_ok=True)
        suffix={'Darwin':'.plist','Linux':'.service','Windows':'.xml'}.get(self.system)
        if not suffix:raise ValueError('Native automatic startup is unsupported on this OS')
        path=directory/(self.label+suffix);self._owned_file(path);_atomic_bytes(path,self._descriptor())
        return {**self.query(),'files':[str(path)],'status':'prepared'}
    def install(self):
        target=self.registration_path();self._owned_file(target)
        existed=target.exists()
        previous_native={key:self.service.config[key] for key in ('native_label','native_kind','native_registration_path') if key in self.service.config}
        operation=self._write_journal('registering')
        prepared=self.prepare();target.parent.mkdir(parents=True,exist_ok=True)
        _atomic_bytes(target,self._descriptor())
        state=self.query();created=not state['registered']
        try:
            if self.system=='Darwin':
                if state['registered'] and not existed:
                    self._run(['launchctl','bootout',f'gui/{os.getuid()}/{self.label}']);created=True
                if created:self._run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(target)])
                elif not state['running']:self._run(['launchctl','kickstart',f'gui/{os.getuid()}/{self.label}'])
            elif self.system=='Linux':
                self._run(['systemctl','--user','daemon-reload']);self._run(['systemctl','--user','enable','--now',self.label+'.service'])
            elif self.system=='Windows':
                if created:self._run(['schtasks.exe','/Create','/TN',self.label,'/XML',str(target)])
                elif not state['enabled']:self._run(['schtasks.exe','/Change','/TN',self.label,'/ENABLE'])
                self._run(['schtasks.exe','/Run','/TN',self.label])
            self.service.config.update(native_label=self.label,native_kind=self.kind,native_registration_path=str(target))
            self.service.save(self.service.config)
            readback=self.query()
            if not readback['registered']:raise RuntimeError('Native registration command completed but OS readback did not confirm ownership')
            self._write_journal('registration_checked',operation_id=operation['operation_id'])
        except (OSError,RuntimeError,subprocess.SubprocessError):
            if created:
                try:
                    self.stop()
                    if self.system=='Windows':self._run(['schtasks.exe','/Delete','/TN',self.label,'/F'])
                    elif self.system=='Linux':self._run(['systemctl','--user','disable',self.label+'.service'],allow_failure=True)
                    target.unlink(missing_ok=True)
                    if self.system=='Linux':self._run(['systemctl','--user','daemon-reload'])
                except (OSError,RuntimeError,subprocess.SubprocessError):
                    # Keep the owned descriptor available for explicit recovery.
                    pass
            self._write_journal('registration_failed',operation_id=operation['operation_id'],error='Native registration failed; verify owned descriptor and OS state before retrying')
            for key in ('native_label','native_kind','native_registration_path'):self.service.config.pop(key,None)
            self.service.config.update(previous_native);self.service.save(self.service.config)
            raise
        return {**self.query(),'files':prepared['files'],'status':'installed','readiness':'Check the live inspection runtime separately'}
    def stop(self):
        state=self.query()
        if not state['registered']:return state
        if self.system=='Darwin':self._run(['launchctl','bootout',f'gui/{os.getuid()}/{self.label}'])
        elif self.system=='Linux':self._run(['systemctl','--user','stop',self.label+'.service'])
        elif self.system=='Windows':self._run(['schtasks.exe','/End','/TN',self.label],allow_failure=True)
        return self.query()
    def remove(self):
        target=self.registration_path();self._owned_file(target);state=self.query()
        if state.get('error') and self.service.config.get('native_kind'):raise RuntimeError('Native registration readback failed; owned records retained: '+state['error'])
        operation=self._write_journal('removing')
        self.stop()
        if self.system=='Linux' and state['registered']:self._run(['systemctl','--user','disable',self.label+'.service'])
        elif self.system=='Windows' and state['registered']:self._run(['schtasks.exe','/Delete','/TN',self.label,'/F'])
        target.unlink(missing_ok=True)
        if self.system=='Linux':self._run(['systemctl','--user','daemon-reload'])
        for key in ('native_label','native_kind','native_registration_path'):self.service.config.pop(key,None)
        self.service.save(self.service.config)
        self._write_journal('removed',operation_id=operation['operation_id'])
        return {**self.query(),'status':'uninstalled'}
