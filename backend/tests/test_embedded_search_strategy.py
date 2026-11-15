"""Actual Optuna ask/tell plus fake owned training, never model/GPU training."""
import json
import threading
from pathlib import Path

import pytest
import torch


def strategy_module():
    from backend.engine import search_strategy
    return search_strategy


def test_actual_sampler_feedback_changes_later_unique_proposals():
    module = strategy_module()
    domain = {'learning_rate': list(range(16)), 'width': list(range(16))}
    def sequence(sign):
        search = module.CandidateSearch(domain, {}, requested='adaptive', mode='search', max_trials=24, seed=41)
        configs = []
        for i in range(24):
            config, token, proposal = search.ask()
            configs.append(config)
            search.tell(token, sign * (config['learning_rate'] + config['width']))
        assert len({module.configuration_key(row) for row in configs}) == len(configs)
        assert search.identity['optuna_version'] == '5.0.0'
        assert search.identity['sampler_options']['n_startup_trials'] == 4
        return configs
    low, high = sequence(1), sequence(-1)
    assert low[:4] == high[:4]
    assert low[4:] != high[4:], 'measured feedback must affect subsequent asks'


def test_bounded_duplicate_asks_fall_back_lazily_without_execution_slots():
    module = strategy_module()
    search = module.CandidateSearch({'width': [8, 8, 16]}, {}, requested='adaptive', mode='search', max_trials=8, seed=0)
    seen = []
    for _ in range(2):
        config, token, proposal = search.ask(); seen.append(config)
        search.tell(token, float(config['width']))
    with pytest.raises(StopIteration): search.ask()
    assert {row['width'] for row in seen} == {8, 16}
    assert search.proposal_count == 2
    assert search.identity['pruning']['available'] is False


def test_auto_threshold_quick_behavior_and_explicit_missing_runtime(monkeypatch):
    module = strategy_module()
    assert module.resolve_strategy('auto', 'search', 7) == 'deterministic'
    assert module.resolve_strategy('auto', 'search', 8) == 'adaptive'
    assert module.resolve_strategy('adaptive', 'quick', 8) == 'deterministic'
    monkeypatch.setattr(module, '_load_optuna', lambda: (_ for _ in ()).throw(RuntimeError('Optuna unavailable')))
    assert module.CandidateSearch({'width': [8]}, {}, requested='auto', mode='search', max_trials=4, seed=0).identity['resolved_strategy'] == 'deterministic'
    with pytest.raises(RuntimeError, match='Optuna unavailable'):
        module.CandidateSearch({'width': [8]}, {}, requested='adaptive', mode='search', max_trials=8, seed=0)


@pytest.fixture
def fake_search(tmp_path, monkeypatch):
    from backend.engine import automated_trials as trials
    source = tmp_path/'source'; source.mkdir(); (source/'input.txt').write_text('immutable')
    models = tmp_path/'models'; seen=[]
    def run(context):
        seen.append(dict(context.config))
        context.output_dir.mkdir(exist_ok=True)
        torch.save({'task': 'classification'}, context.output_dir/'best_model.pt')
        (context.output_dir/'model_meta.json').write_text(json.dumps({'task':'classification'}))
        return {'checkpoint_path':str(context.output_dir/'best_model.pt'), 'metrics':{'val_loss':float(context.config['learning_rate'])}, 'latency_ms':2., 'epochs_completed':1}
    monkeypatch.setitem(trials._RUNNERS, 'classification', trials.TaskRunner(run, ('dinov3_vits16',)))
    from contextlib import nullcontext
    monkeypatch.setattr('backend.engine.shared_scheduler.compute_lease_scope', lambda *a,**k:nullcontext())
    args=dict(task='classification',dataset_path=source,models_dir=models,mode='search',epochs_per_trial=1,
        budget={'max_trials':8,'max_total_epochs':8,'max_seconds':60},search_strategy='adaptive',seed=17,
        search_space={'architectures':['dinov3_vits16'],'learning_rates':[.001,.001,.01,.1], 'weight_decays':[0], 'image_sizes':[32], 'batch_sizes':[1], 'augmentation_profiles':['none']})
    return trials,args,seen


def test_feedback_only_after_durable_receipt_same_signed_latency_objective(fake_search, monkeypatch):
    trials,args,seen = fake_search
    module=strategy_module(); original=module.CandidateSearch.tell; feedback=[]
    def tell(self, token, objective=None):
        if objective is not None:
            receipts=list(Path(args['models_dir']).glob('job_*/job_receipt.json'))
            completed=[json.loads(path.read_text()) for path in receipts if json.loads(path.read_text())['status']=='completed']
            assert len(completed)==len(feedback)+1
            assert all(Path(row['output_dir'],'best_model.pt').is_file() for row in completed)
            feedback.append(objective)
        return original(self,token,objective)
    monkeypatch.setattr(module.CandidateSearch,'tell',tell)
    result=trials.run_automated_training(**args,base_config={'objective':'loss_latency','latency_weight':.25})
    assert result['status']=='completed',result
    assert len(seen)==3, 'duplicates do not spend trial or epoch budget, including different ordinal seeds'
    assert result['epochs_consumed']==3
    assert feedback == [row['objective'] for row in result['trials']]
    assert feedback == [row['config']['learning_rate']+.5 for row in result['trials']]
    assert [row['proposal_ordinal'] for row in result['trials']]==[0,1,2]
    assert [row['config']['seed'] for row in result['trials']]==[17,18,19]
    saved=json.loads((Path(args['models_dir'])/'automated_training'/result['search_id']/'search.json').read_text())
    assert saved['search_sampling']==result['search_sampling']
    assert saved['search_sampling']['domain_sha256']


def test_failures_cancellation_and_bad_measurements_never_feed_complete(fake_search, monkeypatch):
    trials,args,seen=fake_search
    module=strategy_module(); original=module.CandidateSearch.tell; complete=[]
    def tell(self, token, objective=None):
        if objective is not None: complete.append(objective)
        return original(self,token,objective)
    monkeypatch.setattr(module.CandidateSearch,'tell',tell)
    def bad(context):
        seen.append(context.config); raise ValueError('invalid heldout receipt')
    monkeypatch.setitem(trials._RUNNERS,'classification',trials.TaskRunner(bad,('dinov3_vits16',)))
    result=trials.run_automated_training(**args)
    assert result['status']=='failed' and result['winner'] is None and not complete
    event=threading.Event();event.set()
    result=trials.run_automated_training(**{**args,'models_dir':Path(args['models_dir']).with_name('cancel')},cancel_event=event)
    assert result['status']=='cancelled' and result['trials']==[] and result['winner'] is None


def test_recipe_rest_and_cli_strategy_are_separate_from_training_config():
    from backend.engine.training_engine import configuration_recipe, capabilities
    from backend.training_cli import parser
    from backend.api.routes_training_engine import TrainRequest
    from backend.api.routes_automated_training import StartRequest
    recipe={'search_strategy':'adaptive','config':{'seed':7},'output_dir':'x'}
    assert configuration_recipe(recipe)==recipe
    assert parser().parse_args(['train','--output','x','--search-strategy','adaptive']).search_strategy=='adaptive'
    assert TrainRequest(output_dir='x',search_strategy='adaptive').search_strategy=='adaptive'
    assert StartRequest(dataset_path='x',search_strategy='adaptive').search_strategy=='adaptive'
    assert capabilities()['search_sampling']['pruning']['available'] is False


def test_forced_duplicate_sampler_reaches_enumeration_with_bounded_asks(monkeypatch):
    module=strategy_module()
    search=module.CandidateSearch({'width':[8,16]}, {},requested='adaptive',mode='search',max_trials=8,seed=0)
    monkeypatch.setattr(search.study.sampler,'sample_independent',lambda study,trial,name,distribution:distribution.choices[0])
    first,token,_=search.ask();search.tell(token,1.)
    second,token,proposal=search.ask();search.tell(token,2.)
    assert first != second and proposal['proposal_source']=='enumeration'
    assert len(search.study.trials)==module.DUPLICATE_ASK_LIMIT+2


def test_reuse_rejects_missing_durable_completed_receipt(fake_search):
    trials,args,seen=fake_search
    first=trials.run_automated_training(**args)
    Path(first['winner']['checkpoint_path']).with_name('job_receipt.json').unlink()
    with pytest.raises(ValueError,match='receipt'):
        trials.run_automated_training(**args,reuse_search_id=first['search_id'])


def test_cancel_during_receipt_publication_never_tells_complete(fake_search,monkeypatch):
    trials,args,seen=fake_search;module=strategy_module();event=threading.Event();feedback=[]
    original_write=trials._write;original_tell=module.CandidateSearch.tell
    def write(path,value):
        original_write(path,value)
        if Path(path).name=='job_receipt.json' and value.get('status')=='completed':event.set()
    def tell(self,token,objective=None):
        if objective is not None:feedback.append(objective)
        original_tell(self,token,objective)
    monkeypatch.setattr(trials,'_write',write);monkeypatch.setattr(module.CandidateSearch,'tell',tell)
    result=trials.run_automated_training(**args,cancel_event=event)
    assert result['status']=='cancelled' and result['winner'] is None
    assert not feedback
    assert result['trials'][0]['status']=='cancelled'


@pytest.mark.parametrize('field,value',[('val_loss',float('nan')),('val_loss',True),('latency_ms',0.),('latency_ms',float('inf')),('epochs_completed',0),('epochs_completed',2)])
def test_invalid_measured_results_do_not_enter_feedback(fake_search,monkeypatch,field,value):
    trials,args,_=fake_search;original=trials._RUNNERS['classification'];module=strategy_module();feedback=[]
    def run(context):
        result=original.run(context)
        if field=='val_loss':result['metrics'][field]=value
        else:result[field]=value
        return result
    monkeypatch.setitem(trials._RUNNERS,'classification',trials.TaskRunner(run,original.architectures))
    original_tell=module.CandidateSearch.tell
    def tell(self,token,objective=None):
        if objective is not None:feedback.append(objective)
        return original_tell(self,token,objective)
    monkeypatch.setattr(module.CandidateSearch,'tell',tell)
    result=trials.run_automated_training(**args)
    assert result['status']=='failed' and result['winner'] is None and feedback==[]


def test_engine_recipe_replay_and_idempotency_bind_strategy(fake_search,monkeypatch,tmp_path):
    from contextlib import nullcontext
    from backend.engine import training_engine as engine
    from backend.engine.job_store import JobConflict
    trials,args,seen=fake_search
    output=tmp_path/'engine'
    prepared={'task':'classification','prepared_id':'a'*32,'dataset_path':str(args['dataset_path']),
        'source_dataset_path':str(args['dataset_path']),'training_binding':None,'project':{}}
    monkeypatch.setattr(engine,'_prepared',lambda *a:prepared)
    monkeypatch.setattr(engine,'workspace_scope',lambda *a:nullcontext())
    options={'output_dir':output,'mode':'search','search_strategy':'adaptive','seed':17,
        'search_space':args['search_space'],'epochs_per_trial':1,'budget':args['budget'],'idempotency_key':'adaptive-replay'}
    initial=engine.create_run(**options)
    repeated=engine.create_run(**options)
    assert repeated['run_id']==initial['run_id'] and repeated['idempotent_replay'] is True
    with pytest.raises(JobConflict):engine.create_run(**{**options,'search_strategy':'deterministic'})
    result=engine.execute_run(output,initial['run_id'])
    assert result['status']=='completed',result
    recipe=json.loads(Path(result['artifacts']['configuration']['path']).read_text())
    assert recipe['search_strategy']=='adaptive' and recipe['seed']==17
    replay=engine.create_run(**engine.configuration_recipe(recipe))
    assert replay['search_sampling']==initial['search_sampling']


def test_recipe_persists_sampler_identity_and_winning_ordinal(fake_search,monkeypatch,tmp_path):
    from contextlib import nullcontext
    from backend.engine import training_engine as engine
    trials,args,_=fake_search
    prepared={'task':'classification','prepared_id':'a'*32,'dataset_path':str(args['dataset_path']),
        'source_dataset_path':str(args['dataset_path']),'training_binding':None,'project':{}}
    monkeypatch.setattr(engine,'_prepared',lambda *a:prepared);monkeypatch.setattr(engine,'workspace_scope',lambda *a:nullcontext())
    output=tmp_path/'recipe';run=engine.create_run(output_dir=output,mode='search',search_strategy='adaptive',seed=9,
        search_space=args['search_space'],budget=args['budget'],epochs_per_trial=1)
    result=engine.execute_run(output,run['run_id'])
    recipe=json.loads(Path(result['artifacts']['configuration']['path']).read_text())
    assert recipe['search_sampling']==result['search']['search_sampling']
    assert recipe['winning_proposal']['proposal_ordinal']==result['winner']['proposal_ordinal']


def test_maximizing_runner_feedback_preserves_existing_sign_and_latency(fake_search,monkeypatch):
    trials,args,_=fake_search;spec=trials._RUNNERS['classification']
    monkeypatch.setitem(trials._RUNNERS,'classification',trials.TaskRunner(spec.run,spec.architectures,direction='max'))
    result=trials.run_automated_training(**args,base_config={'objective':'loss_latency','latency_weight':.5})
    assert result['winner']['config']['learning_rate']==.1
    assert all(row['objective']==-row['metrics']['val_loss']+1 for row in result['trials'])


@pytest.mark.parametrize('budget,reason,executions',[
    ({'max_trials':1,'max_total_epochs':8,'max_seconds':60},'trial_budget',1),
    ({'max_trials':8,'max_total_epochs':1,'max_seconds':60},'epoch_budget',1),
    ({'max_trials':8,'max_total_epochs':8,'max_seconds':60,'max_memory_mb':1},'memory_budget',0)])
def test_existing_execution_budget_precedes_additional_candidates(fake_search,budget,reason,executions):
    trials,args,seen=fake_search
    result=trials.run_automated_training(**{**args,'budget':budget})
    assert result['stop_reason']==reason and len(seen)==executions
    assert result['epochs_consumed']==executions
    if executions==0:assert result['winner'] is None


def test_time_budget_after_worker_result_cannot_feed_sampler(fake_search,monkeypatch):
    import types,time
    trials,args,seen=fake_search;module=strategy_module();clock={'now':0.};feedback=[]
    monkeypatch.setattr(trials,'time',types.SimpleNamespace(monotonic=lambda:clock['now'],time=time.time))
    spec=trials._RUNNERS['classification']
    def run(context):
        result=spec.run(context);clock['now']=61.;return result
    monkeypatch.setitem(trials._RUNNERS,'classification',trials.TaskRunner(run,spec.architectures))
    original=module.CandidateSearch.tell
    def tell(self,token,objective=None):
        if objective is not None:feedback.append(objective)
        return original(self,token,objective)
    monkeypatch.setattr(module.CandidateSearch,'tell',tell)
    result=trials.run_automated_training(**args)
    assert result['stop_reason']=='time_budget' and result['winner'] is None and not feedback
    assert len(seen)==1 and result['trials'][0]['status']=='cancelled'


def test_remote_unknown_keeps_owned_receipt_and_stops_without_winner(fake_search,monkeypatch):
    from types import SimpleNamespace
    from backend.engine import remote_automated_trials as remote
    from backend.remote import profiles
    trials,args,seen=fake_search
    profile=SimpleNamespace(id='owned')
    monkeypatch.setattr(profiles,'get_profile_store',lambda:SimpleNamespace(get=lambda _:profile))
    monkeypatch.setattr(remote,'validate_remote_search',lambda *a,**k:profile)
    calls=[]
    def uncertain(context,**kwargs):
        calls.append(context.search_sampling)
        raise remote.RemoteTrialUncertain('owned worker disconnected')
    monkeypatch.setattr(remote,'run_remote_candidate',uncertain)
    result=trials.run_automated_training(**args,compute_profile_id='owned')
    assert result['status']=='interrupted' and result['stop_reason']=='remote_reconciliation_required' and result['winner'] is None
    assert len(calls)==1 and not seen
    assert calls[0]['resolved_strategy']=='adaptive' and calls[0]['sampler_execution']=='coordinator'
    assert list(Path(args['models_dir']).glob('job_*/job_receipt.json')), 'unknown remote receipt stays owned for reconciliation'


def test_remote_probe_validates_all_finite_alternatives_even_for_small_budget(monkeypatch):
    from backend.engine.remote_automated_trials import validate_remote_search
    from backend.tests.test_extension_remote_autodl import profile
    from backend.remote import ssh_transport
    seen=[]
    monkeypatch.setattr(ssh_transport.SSHTransport,'probe',lambda *a:{'ready':True,'runtime_ready':True,'checks':{}})
    def require(readiness,task,preset,config,**kwargs):
        seen.append(config['backbone'])
        if config['backbone']=='dinov3_vitb16':raise RuntimeError('selected worker does not support this categorical model')
    monkeypatch.setattr(ssh_transport,'require_training_runtime',require)
    with pytest.raises(RuntimeError,match='categorical model'):
        validate_remote_search(profile(),'classification','fast','cpu',{}, {'architectures':['dinov3_vits16','dinov3_vitb16']},{'max_trials':1})
    assert 'dinov3_vitb16' in seen


def test_invalid_strategy_and_runtime_fail_before_execution_or_journal(fake_search,monkeypatch):
    trials,args,seen=fake_search;module=strategy_module()
    with pytest.raises(ValueError,match='strategy'):trials.run_automated_training(**{**args,'search_strategy':'unknown'})
    monkeypatch.setattr(module,'_load_optuna',lambda: (_ for _ in ()).throw(RuntimeError('incompatible bundled Optuna')))
    with pytest.raises(RuntimeError,match='Optuna'):trials.run_automated_training(**args)
    assert not seen and not Path(args['models_dir']).exists()


def test_cancel_from_completed_progress_precedes_sampler_feedback(fake_search,monkeypatch):
    trials,args,_=fake_search;module=strategy_module();event=threading.Event();feedback=[]
    original=module.CandidateSearch.tell
    def tell(self,token,objective=None):
        if objective is not None:feedback.append(objective)
        return original(self,token,objective)
    monkeypatch.setattr(module.CandidateSearch,'tell',tell)
    def progress(record):
        if any(row['status']=='completed' for row in record['trials']):event.set()
    result=trials.run_automated_training(**args,cancel_event=event,on_progress=progress)
    assert result['status']=='cancelled' and result['winner'] is None and not feedback
    assert result['epochs_consumed']==1


def test_reuse_only_verified_completed_feedback_without_new_execution(fake_search):
    trials,args,seen=fake_search
    initial=trials.run_automated_training(**args)
    original=[Path(row['checkpoint_path']).read_bytes() for row in initial['trials']]
    reused=trials.run_automated_training(**args,reuse_search_id=initial['search_id'])
    assert reused['status']=='completed',reused
    assert len(seen)==3 and reused['epochs_consumed']==0
    assert all(row['reused_from_search_id']==initial['search_id'] for row in reused['trials'])
    assert original==[Path(row['checkpoint_path']).read_bytes() for row in initial['trials']]
    with pytest.raises(ValueError,match='identity'):
        trials.run_automated_training(**{**args,'search_strategy':'deterministic'},reuse_search_id=initial['search_id'])


def test_reserved_strategy_cannot_hide_in_model_training_config(fake_search):
    trials,args,seen=fake_search
    with pytest.raises(ValueError,match='strategy|sampling'):
        trials.run_automated_training(**args,base_config={'search_strategy':'deterministic'})
    assert not seen


def test_owned_engine_rest_and_cli_replay_preserve_strategy_without_training(fake_search,monkeypatch,tmp_path,capsys):
    from contextlib import nullcontext
    from backend.engine import training_engine as engine
    from backend.training_cli import main
    from backend.main import create_app
    from fastapi.testclient import TestClient
    trials,args,_=fake_search
    prepared={'task':'classification','prepared_id':'a'*32,'dataset_path':str(args['dataset_path']),
        'source_dataset_path':str(args['dataset_path']),'training_binding':None,'project':{}}
    monkeypatch.setattr(engine,'_prepared',lambda *a:prepared);monkeypatch.setattr(engine,'workspace_scope',lambda *a:nullcontext())
    monkeypatch.setattr('backend.training_cli.signal.signal',lambda *a:None)
    app=create_app(project_dir=str(tmp_path/'desktop'));api=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    output=tmp_path/'rest';request={'output_dir':str(output),'mode':'search','search_strategy':'adaptive','seed':23,
        'search_space':args['search_space'],'budget':args['budget'],'epochs_per_trial':1,'background':False}
    response=api.post('/api/engine/train',json=request,headers={'Idempotency-Key':'sampler-rest'})
    assert response.status_code==200,response.text
    result=response.json();assert result['status']=='completed',result
    assert result['search']['search_sampling']['resolved_strategy']=='adaptive'
    repeat=api.post('/api/engine/train',json=request,headers={'Idempotency-Key':'sampler-rest'})
    assert repeat.json()['run_id']==result['run_id'] and repeat.json()['idempotent_replay'] is True
    conflict=api.post('/api/engine/train',json={**request,'search_strategy':'deterministic'},headers={'Idempotency-Key':'sampler-rest'})
    assert conflict.status_code==409,conflict.text
    assert main(['train','--config-path',result['artifacts']['configuration']['path']])==0
    rows=[json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith('{')]
    cli=rows[-1];assert cli['search_sampling']==result['search_sampling'] and cli['seed']==23
    assert cli['search']['search_sampling']==result['search']['search_sampling']


def test_numeric_equivalent_categories_keep_existing_configuration_uniqueness():
    module=strategy_module()
    search=module.CandidateSearch({'learning_rate':[1,1.]}, {},requested='deterministic',mode='search',max_trials=4,seed=0)
    assert search.ask()[0]['learning_rate']==1
    with pytest.raises(StopIteration):search.ask()


def test_small_auto_preserves_diagonal_then_cartesian_order_and_quick_rectangles():
    from backend.engine.automated_trials import validated_domain,_space
    module=strategy_module()
    base={};space={'architectures':['dinov3_vits16','dinov3_vitb16'],'learning_rates':[.01,.001],
        'weight_decays':[0,.1],'image_sizes':[32],'batch_sizes':[1],'augmentation_profiles':['none','industrial']}
    domain=validated_domain('classification',space,base,'search')
    search=module.CandidateSearch(domain,base,requested='auto',mode='search',max_trials=4,seed=0)
    configs=[search.ask()[0] for _ in range(4)]
    assert [row['backbone'] for row in configs[:2]]==['dinov3_vits16','dinov3_vitb16']
    assert [row['learning_rate'] for row in configs[:2]]==[.01,.001]
    assert configs==list(__import__('itertools').islice(_space('classification',space,base,'search'),4))
    quick=module.CandidateSearch(validated_domain('classification',None,{'image_size':[32,48]},'quick'),{},
        requested='adaptive',mode='quick',max_trials=8,seed=0)
    assert quick.ask()[0]['image_size']==[32,48]
    assert quick.identity['resolved_strategy']=='deterministic'


def test_linked_metadata_is_not_a_valid_owned_completed_artifact(fake_search,monkeypatch,tmp_path):
    trials,args,_=fake_search;spec=trials._RUNNERS['classification']
    external=tmp_path/'outside.json';external.write_text('{"task":"classification"}')
    def linked(context):
        result=spec.run(context);metadata=context.output_dir/'model_meta.json'
        metadata.unlink();metadata.symlink_to(external);return result
    monkeypatch.setitem(trials._RUNNERS,'classification',trials.TaskRunner(linked,spec.architectures))
    result=trials.run_automated_training(**args)
    assert result['status']=='failed' and result['winner'] is None
    assert all(row['status']=='failed' for row in result['trials'])
    assert external.read_text()=='{"task":"classification"}'


def test_escaped_candidate_directory_cleanup_does_not_delete_external_artifacts(fake_search,monkeypatch,tmp_path):
    import shutil
    trials,args,_=fake_search;spec=trials._RUNNERS['classification']
    external=tmp_path/'outside';external.mkdir()
    torch.save({'task':'classification'},external/'best_model.pt')
    (external/'model_meta.json').write_text('{"task":"classification"}')
    before={path.name:path.read_bytes() for path in external.iterdir()}
    def escaped(context):
        shutil.rmtree(context.output_dir);context.output_dir.symlink_to(external,target_is_directory=True)
        return {'checkpoint_path':str(context.output_dir/'best_model.pt'),'metrics':{'val_loss':.5},'latency_ms':1.,'epochs_completed':1}
    monkeypatch.setitem(trials._RUNNERS,'classification',trials.TaskRunner(escaped,spec.architectures))
    result=trials.run_automated_training(**args)
    assert result['status']=='failed' and result['winner'] is None
    assert {path.name:path.read_bytes() for path in external.iterdir()}==before


@pytest.mark.parametrize('task',['detection','anomaly','rotated_detection'])
def test_generated_winning_seed_is_replayable_for_every_measured_family(fake_search,monkeypatch,tmp_path,task):
    from backend.engine import training_engine as engine
    prepared={'task':task,'prepared_id':'a'*32,'dataset_path':str(fake_search[1]['dataset_path']),
        'source_dataset_path':str(fake_search[1]['dataset_path'])}
    monkeypatch.setattr(engine,'_prepared',lambda *a:prepared)
    recipe={'output_dir':str(tmp_path/task),'mode':'quick','search_strategy':'adaptive','seed':7,'config':{'seed':9}}
    record=engine.create_run(**engine.configuration_recipe(recipe))
    assert record['seed']==7 and record['config']['seed']==9
    assert record['search_sampling']['resolved_strategy']=='deterministic'


def test_engine_journal_uses_execution_domain_identity_after_parent_resolution(fake_search,monkeypatch,tmp_path):
    from contextlib import nullcontext
    from backend.engine import training_engine as engine
    trials,args,_=fake_search
    prepared={'task':'classification','prepared_id':'a'*32,'dataset_path':str(args['dataset_path']),
        'source_dataset_path':str(args['dataset_path']),'training_binding':None,'project':{}}
    monkeypatch.setattr(engine,'_prepared',lambda *a:prepared);monkeypatch.setattr(engine,'workspace_scope',lambda *a:nullcontext())
    run=engine.create_run(output_dir=tmp_path/'journal')
    executed={**run['search_sampling'],'domain_sha256':'b'*64}
    def resolved(**kwargs):
        result={'search_sampling':executed,'status':'failed','winner':None,'trials':[]}
        kwargs['on_progress'](result);return result
    monkeypatch.setattr(trials,'run_automated_training',resolved)
    final=engine.execute_run(tmp_path/'journal',run['run_id'])
    assert final['search_sampling']==executed
