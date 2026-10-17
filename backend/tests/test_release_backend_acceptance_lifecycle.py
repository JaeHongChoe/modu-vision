"""Owned release acceptance lifecycle controls; no real processes or threads.

The original helper must be tested first. This file's fake Popen/Thread objects
are exclusively created by the fixture, with no PID discovery or signaling.
"""
from types import SimpleNamespace
import subprocess
import pytest
from scripts import release_backend_acceptance as acceptance


class OwnedPipe:
    def __init__(self, events, *, lines=(), read_error=None, close_error=None):
        self.events=events;self.lines=lines;self.read_error=read_error
        self.close_error=close_error;self.close_calls=0
    def __iter__(self):
        if self.read_error is not None:raise self.read_error
        return iter(self.lines)
    def close(self):
        self.close_calls+=1;self.events.append(('stdout.close',self.close_calls))
        if self.close_error is not None:raise self.close_error


class OwnedProcess:
    def __init__(self, events, pipe, *, exited=False, poll_error=None,
                 terminate_error=None, waits=(), kill_error=None):
        self.events=events;self.stdout=pipe;self.returncode=0 if exited else None
        self.poll_error=poll_error;self.terminate_error=terminate_error
        self.waits=list(waits);self.kill_error=kill_error
    def poll(self):
        self.events.append(('poll',))
        if self.poll_error is not None:raise self.poll_error
        return self.returncode
    def terminate(self):
        self.events.append(('terminate',))
        if self.terminate_error is not None:raise self.terminate_error
    def wait(self, timeout):
        self.events.append(('wait',timeout))
        value=self.waits.pop(0) if self.waits else 0
        if isinstance(value,BaseException):raise value
        self.returncode=value;return value
    def kill(self):
        self.events.append(('kill',))
        if self.kill_error is not None:raise self.kill_error


class OwnedReader:
    def __init__(self, events, *, alive=False, join_error=None):
        self.events=events;self.alive=alive;self.join_error=join_error
        self._release_acceptance_errors=[]
    def join(self, timeout):
        self.events.append(('reader.join',timeout))
        if self.join_error is not None:raise self.join_error
    def is_alive(self):
        self.events.append(('reader.is_alive',));return self.alive


def owned_pair(**kwargs):
    events=[];pipe=OwnedPipe(events,close_error=kwargs.pop('close_error',None))
    reader=OwnedReader(events,alive=kwargs.pop('reader_alive',False),
                       join_error=kwargs.pop('join_error',None))
    process=OwnedProcess(events,pipe,**kwargs)
    return events,pipe,process,reader


def assert_closed(events,pipe):
    assert events.count(('reader.join',2))==1
    assert events.index(('reader.is_alive',))<events.index(('stdout.close',1))
    assert pipe.close_calls==1


def test_shutdown_proves_owned_reader_then_closes_stdout_once():
    events,pipe,process,reader=owned_pair()
    acceptance.shutdown(process,reader)
    assert process.returncode==0
    assert ('wait',15) in events
    assert_closed(events,pipe)


def test_already_exited_owned_child_is_not_signaled_and_stdout_closes():
    events,pipe,process,reader=owned_pair(exited=True)
    acceptance.shutdown(process,reader)
    assert not any(row[0] in ('terminate','kill') for row in events)
    assert_closed(events,pipe)


def test_original_terminate_wait_kill_wait_timeouts_remain_exact():
    timeout=subprocess.TimeoutExpired(['owned'],15)
    events,pipe,process,reader=owned_pair(waits=[timeout,0])
    acceptance.shutdown(process,reader)
    assert [row for row in events if row[0] in ('terminate','wait','kill')]==[
        ('terminate',),('wait',15),('kill',),('wait',10)]
    assert_closed(events,pipe)


def test_terminate_error_is_primary_even_when_other_cleanup_succeeds():
    first=OSError('owned terminate failed')
    events,pipe,process,reader=owned_pair(terminate_error=first)
    with pytest.raises(OSError) as caught:acceptance.shutdown(process,reader)
    assert caught.value is first and process.returncode==0
    assert_closed(events,pipe)


def test_wait_and_stdout_failures_preserve_first_and_secondary_objects():
    first=OSError('owned final wait failed');later=OSError('owned close uncertain')
    events,pipe,process,reader=owned_pair(waits=[first],close_error=later)
    with pytest.raises(OSError) as caught:acceptance.shutdown(process,reader)
    assert caught.value is first
    assert caught.value.__cause__.errors[0] is later
    assert pipe.close_calls==1
    assert ('reader.join',2) in events


def test_kill_failure_still_performs_final_owned_wait_and_reader_close():
    first=OSError('owned kill failed')
    events,pipe,process,reader=owned_pair(waits=[subprocess.TimeoutExpired(['owned'],15),0],kill_error=first)
    with pytest.raises(OSError) as caught:acceptance.shutdown(process,reader)
    assert caught.value is first and ('wait',10) in events
    assert_closed(events,pipe)


def test_poll_failure_cannot_skip_wait_reader_or_stdout_cleanup():
    first=OSError('owned poll failed')
    events,pipe,process,reader=owned_pair(poll_error=first)
    with pytest.raises(OSError) as caught:acceptance.shutdown(process,reader)
    assert caught.value is first and ('wait',15) in events
    assert ('terminate',) not in events
    assert_closed(events,pipe)


def test_active_reader_refuses_and_does_not_block_on_its_buffered_pipe_close():
    events,pipe,process,reader=owned_pair(reader_alive=True)
    with pytest.raises(RuntimeError,match='reader') as caught:
        acceptance.shutdown(process,reader)
    assert pipe.close_calls==0
    assert any('stdout' in str(error) for error in caught.value.__cause__.errors)
    assert events.count(('reader.join',2))==1


def test_uncertain_stdout_close_is_attempted_once_and_refuses():
    first=OSError('owned stdout uncertain')
    events,pipe,process,reader=owned_pair(close_error=first)
    with pytest.raises(OSError) as caught:acceptance.shutdown(process,reader)
    assert caught.value is first and pipe.close_calls==1


def test_consumer_failure_is_retained_instead_of_acceptance_success():
    first=OSError('owned reader failed')
    events,pipe,process,reader=owned_pair()
    reader._release_acceptance_errors.append(first)
    with pytest.raises(OSError) as caught:acceptance.shutdown(process,reader)
    assert caught.value is first
    assert_closed(events,pipe)


def test_reader_join_error_is_primary_and_stdout_still_closes_if_inactive():
    first=OSError('owned join failed')
    events,pipe,process,reader=owned_pair(join_error=first)
    with pytest.raises(OSError) as caught:acceptance.shutdown(process,reader)
    assert caught.value is first and pipe.close_calls==1


def launch_fixture(monkeypatch, *, thread_error=None, start_error=None, health_error=None, close_error=None):
    events=[];pipe=OwnedPipe(events,close_error=close_error)
    process=OwnedProcess(events,pipe)
    readers=[]
    monkeypatch.setattr(acceptance.subprocess,'Popen',lambda *a,**kw:process)
    def thread(*,target,daemon):
        assert daemon is True
        if thread_error is not None:raise thread_error
        reader=OwnedReader(events)
        def start():
            events.append(('reader.start',))
            if start_error is not None:raise start_error
            target()
        reader.start=start;readers.append(reader);return reader
    monkeypatch.setattr(acceptance.threading,'Thread',thread)
    def health(*args):
        if health_error is not None:raise health_error
        return {'status':'ready'}
    monkeypatch.setattr(acceptance,'request',health)
    return events,pipe,process,readers


def test_reader_construction_failure_closes_the_already_owned_child(monkeypatch):
    first=RuntimeError('reader construction failed')
    events,pipe,process,readers=launch_fixture(monkeypatch,thread_error=first)
    with pytest.raises(RuntimeError) as caught:
        acceptance.launch('owned',[],'.',{'VISION_AI_STUDIO_API_TOKEN':'fixture'},port=1)
    assert caught.value is first and process.returncode==0 and pipe.close_calls==1
    assert readers==[]


def test_reader_start_failure_closes_same_owned_child_and_pipe(monkeypatch):
    first=RuntimeError('owned reader start failed')
    events,pipe,process,readers=launch_fixture(monkeypatch,start_error=first)
    with pytest.raises(RuntimeError) as caught:
        acceptance.launch('owned',[],'.',{'VISION_AI_STUDIO_API_TOKEN':'fixture'},port=1)
    assert caught.value is first and process.returncode==0 and pipe.close_calls==1
    assert_closed(events,pipe)


def test_launch_primary_survives_cleanup_stdout_error(monkeypatch):
    first=RuntimeError('health original');later=OSError('stdout secondary')
    events,pipe,process,readers=launch_fixture(monkeypatch,health_error=first,close_error=later)
    with pytest.raises(RuntimeError) as caught:
        acceptance.launch('owned',[],'.',{'VISION_AI_STUDIO_API_TOKEN':'fixture'},port=1)
    assert caught.value is first and caught.value.__cause__ is later
    assert pipe.close_calls==1 and process.returncode==0


def test_validate_cannot_report_passed_with_an_active_owned_reader(tmp_path,monkeypatch):
    import json
    binary=tmp_path/'vision_ai_backend';binary.write_bytes(b'owned fixture only')
    identity='exact-build-fixture'
    release={'executable':binary.name,'executable_sha256':acceptance.sha256(binary),
        'inventory':{'platform':'ControlledOS','architecture':'fixture','build_identity_sha256':identity},'files':[]}
    (tmp_path/'backend-release.json').write_text(json.dumps(release))
    monkeypatch.setattr(acceptance.platform,'system',lambda:'ControlledOS')
    monkeypatch.setattr(acceptance.platform,'machine',lambda:'fixture')
    diagnostic={'frozen':True,'status':'ready','build_identity_sha256':identity}
    monkeypatch.setattr(acceptance.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps(diagnostic),stderr=''))
    events,pipe,process,reader=owned_pair(reader_alive=True)
    process.pid=271828
    monkeypatch.setattr(acceptance,'launch',lambda *a,**kw:(process,reader,1,{'status':'ready'},[]))
    with pytest.raises(RuntimeError,match='reader'):acceptance.validate(tmp_path)
    assert pipe.close_calls==0


def test_only_captured_owned_stdout_is_closed_after_identity_replacement():
    events,pipe,process,reader=owned_pair()
    reader._release_acceptance_stdout=pipe
    foreign=OwnedPipe(events);process.stdout=foreign
    with pytest.raises(RuntimeError,match='stdout identity'):
        acceptance.shutdown(process,reader)
    assert pipe.close_calls==1 and foreign.close_calls==0



def validation_fixture(tmp_path,monkeypatch):
    import json
    binary=tmp_path/'vision_ai_backend';binary.write_bytes(b'owned validation fixture only')
    identity='exact-validation-build-fixture'
    release={'executable':binary.name,'executable_sha256':acceptance.sha256(binary),
        'inventory':{'platform':'ControlledOS','architecture':'fixture','build_identity_sha256':identity},'files':[]}
    (tmp_path/'backend-release.json').write_text(json.dumps(release))
    monkeypatch.setattr(acceptance.platform,'system',lambda:'ControlledOS')
    monkeypatch.setattr(acceptance.platform,'machine',lambda:'fixture')
    diagnostic={'frozen':True,'status':'ready','build_identity_sha256':identity}
    monkeypatch.setattr(acceptance.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps(diagnostic),stderr=''))


def test_validate_health_body_primary_survives_same_owned_cleanup_failure(tmp_path,monkeypatch):
    validation_fixture(tmp_path,monkeypatch)
    first=RuntimeError('health body original');cleanup=OSError('health owned stdout cleanup')
    events=[];pipe=OwnedPipe(events,close_error=cleanup);reader=OwnedReader(events)
    class BodyFaultProcess(OwnedProcess):
        @property
        def pid(self):raise first
    process=BodyFaultProcess(events,pipe)
    monkeypatch.setattr(acceptance,'launch',lambda *a,**kw:(process,reader,1,{'status':'ready'},[]))
    with pytest.raises(RuntimeError) as caught:acceptance.validate(tmp_path)
    assert caught.value is first and caught.value.__cause__ is cleanup
    assert process.returncode==0
    assert_closed(events,pipe)


def test_validate_inspection_body_primary_survives_same_owned_cleanup_failure(tmp_path,monkeypatch):
    validation_fixture(tmp_path,monkeypatch)
    first=RuntimeError('inspection body original');cleanup=OSError('inspection owned stdout cleanup')
    owned=[]
    for number in range(3):
        events=[];pipe=OwnedPipe(events,close_error=cleanup if number==2 else None)
        process=OwnedProcess(events,pipe);process.pid=number+1;reader=OwnedReader(events)
        owned.append((events,pipe,process,reader))
    launch_rows=iter(owned)
    def launch(*args,**kwargs):
        events,pipe,process,reader=next(launch_rows)
        return process,reader,1,{'status':'ready'},[]
    monkeypatch.setattr(acceptance,'launch',launch)
    class OwnedProbe:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def bind(self,address):assert address==('127.0.0.1',0)
        def getsockname(self):return ('127.0.0.1',1)
    monkeypatch.setattr(acceptance.socket,'socket',lambda:OwnedProbe())
    def request(*args,**kwargs):raise first
    monkeypatch.setattr(acceptance,'request',request)
    package=tmp_path/'package';package.mkdir();(package/'manifest.json').write_bytes(b'{}')
    image=tmp_path/'image.bin';image.write_bytes(b'owned known image')
    with pytest.raises(RuntimeError) as caught:acceptance.validate(tmp_path,package=package,image=image)
    assert caught.value is first and caught.value.__cause__ is cleanup
    for events,pipe,process,reader in owned:
        assert process.returncode==0
        assert_closed(events,pipe)
