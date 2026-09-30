from backend.engine.evaluation_history import EvaluationHistory


def test_evaluation_history_selects_labelset_without_rebinding_saved_results(tmp_path):
    history=EvaluationHistory(tmp_path)
    first=history.append({'job_id':'job_parent','task':'classification'}, {'labelset_id':'default','parent_job_id':'job_parent','threshold_settings':{'probability':.6}})
    history.append({'job_id':'job_parent','task':'classification'}, {'labelset_id':'ls_123456789abc'})
    assert [r['evaluation_id'] for r in history.list(labelset_id='default')]==[first['evaluation_id']]
    assert history.get(first['evaluation_id'])['binding']['threshold_settings']=={'probability':.6}
