"""Only mocked entry-block dispatch, never actual app/service authority.

The compiled AST is the saved entry __main__ block. Backend imports are exact
test callbacks, not GUI/API/lease/inference imports or approval bypasses. Real
inspection-service argument and release validators stay in their own module.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

_ENTRY=Path(__file__).resolve().parents[2]/'scripts/frozen_backend_entry.py'

def _dispatch(arguments,result=0,service_error=None):
    events=[];sys=SimpleNamespace(argv=['fixture-frozen-backend',*arguments])
    def freeze_support():events.append(('freeze-support',))
    def inspection_main():
        events.append(('inspection-main',tuple(sys.argv)))
        if service_error is not None:raise service_error
        return result
    def owned_main(values):
        events.append(('owned-main',tuple(values)))
        return result
    def bootstrap():events.append(('foreground-bootstrap',))
    def run_module(name,**kwargs):
        events.append(('foreground-module',name,kwargs['run_name'],tuple(sys.argv)))
    owned_modules={'backend.engine.application_launch_controller',
                   'backend.engine.application_launch_execution',
                   'backend.engine.staged_canary_frozen_execution'}
    def exact_import(name,globals=None,locals=None,fromlist=(),level=0):
        assert level==0
        if name=='backend.main':
            assert fromlist==('main',)
            def canonical_main():events.append(('canonical-foreground',tuple(sys.argv)))
            return SimpleNamespace(main=canonical_main)
        if name=='backend.engine.inspection_service':
            assert fromlist==('main',)
            return SimpleNamespace(main=inspection_main)
        if name=='backend.engine.application_launch_handshake':
            assert fromlist==('early_backend_bootstrap',)
            return SimpleNamespace(early_backend_bootstrap=bootstrap)
        if name in owned_modules:
            if name.endswith('application_launch_controller'):
                assert fromlist==('main',)
                return SimpleNamespace(main=owned_main)
            assert fromlist==('frozen_worker_main',)
            return SimpleNamespace(frozen_worker_main=owned_main)
        raise AssertionError('Unexpected backend import in this dispatch case: '+name)
    parsed=ast.parse(_ENTRY.read_bytes())
    blocks=[n for n in parsed.body if isinstance(n,ast.If) and isinstance(n.test,ast.Compare)
        and isinstance(n.test.left,ast.Name) and n.test.left.id=='__name__']
    assert len(blocks)==1
    namespace={'__name__':'__main__','sys':sys,
        'multiprocessing':SimpleNamespace(freeze_support=freeze_support),
        'runpy':SimpleNamespace(run_module=run_module),
        '__builtins__':{**vars(__import__('builtins')),'__import__':exact_import}}
    try:
        exec(compile(ast.Module(body=blocks,type_ignores=[]),str(_ENTRY),'exec'),namespace)
    except SystemExit as error:
        return events,sys.argv,error.code
    return events,sys.argv,None

@pytest.mark.parametrize('result',[0,17])
def test_exact_first_inspection_flag_forwards_all_remaining_arguments_and_exit_result(result):
    rest=['--package','fixture-package','--state-dir','fixture-state',
          '--require-approved-release','--port','12345']
    events,arguments,code=_dispatch(['--inspection-service',*rest],result=result)
    assert code==result
    assert arguments==['fixture-frozen-backend',*rest]
    assert events==[('freeze-support',),('inspection-main',tuple(arguments))]

def test_inspection_validator_exception_is_not_swallowed_or_redirected_to_gui():
    original=ValueError('fixture service validation refusal')
    with pytest.raises(ValueError) as error:
        _dispatch(['--inspection-service','--state-dir','fixture-state'],service_error=original)
    assert error.value is original

@pytest.mark.parametrize('rest',[[],['--inspection-service=1'],
    ['--port','12345','--inspection-service'],['--Inspection-Service']])
def test_absent_nonmatching_or_late_role_keeps_original_foreground_path(rest):
    events,arguments,code=_dispatch(rest)
    assert code is None
    assert arguments==['fixture-frozen-backend',*rest]
    assert events==[('freeze-support',),('foreground-bootstrap',),
        ('canonical-foreground',tuple(arguments))]

@pytest.mark.parametrize('role',['--owned-application-launch-controller',
    '--owned-application-cpu-worker','--owned-staged-canary-cpu-worker'])
def test_existing_owned_roles_keep_precedence_arguments_and_exit_result(role):
    rest=['fixture-owned-argument','--inspection-service']
    events,arguments,code=_dispatch([role,*rest],result=23)
    assert code==23
    assert arguments==['fixture-frozen-backend',role,*rest]
    assert events==[('freeze-support',),('owned-main',tuple(rest))]
