"""S2-05: UNKNOWN is an explicit rule. When every condition toward the decision was evaluated and none was met (a
"defect present" branch on an image without that defect), the decision's no_branch_policy decides (REVIEW unless the
flow says OK or NG). A step that failed, received nothing or answered REVIEW keeps the image incomplete: never OK."""
import numpy as np
import pytest

from backend.engine.flowchart_engine import (CropInspectionResult, FlowchartEngine, FlowchartInspectionLimitError, FlowchartPipeline,
                                             FlowEdge, FlowNode, FlowNodeData, ordered_linear_nodes)

PRESENT = {'kind': 'class', 'operator': 'present', 'class_name': 'scratch', 'min_confidence': 0.5}
IMAGE = np.zeros((32, 32, 3), np.uint8)


def node(node_id, node_type, **data):
    return FlowNode(id=node_id, position={}, data=FlowNodeData(label=node_id, node_type=node_type, **data))


def flow(policy=None, chain=False):
    """input -> model -[scratch present]-> decision -> ok/ng/review; with ``chain``, a second model runs only on images
    where the first saw a scratch, and the decision takes the second model's result."""
    params = {'no_branch_policy': policy} if policy else {}
    nodes = [node('input', 'input'), node('model', 'inspection', task='classification', model_job_id='job_a'),
             node('decision', 'decision', params=params), node('ok', 'output'), node('ng', 'output'), node('review', 'output')]
    edges = [FlowEdge(id='to-model', source='input', target='model')]
    if chain:
        nodes.insert(2, node('detail', 'inspection', task='classification', model_job_id='job_b'))
        edges += [FlowEdge(id='to-detail', source='model', target='detail', payload_type='image', predicate=PRESENT),
                  FlowEdge(id='to-decision', source='detail', target='decision')]
    else:
        edges.append(FlowEdge(id='to-decision', source='model', target='decision', predicate=PRESENT))
    edges += [FlowEdge(id='pass', source='decision', target='ok', isBranch='pass'),
              FlowEdge(id='fail', source='decision', target='ng', isBranch='fail'),
              FlowEdge(id='review', source='decision', target='review', isBranch='review')]
    return FlowchartPipeline(nodes=nodes, edges=edges)


def engine_seeing(monkeypatch, label, *, verdict='OK', fail=None, empty=False, status='passed'):
    engine = FlowchartEngine(device='cpu')

    def inspect(image, rois, inspected):
        if fail == inspected.id:
            raise FlowchartInspectionLimitError('too many tiles')
        if empty:
            return [], 1.0, 'passed'
        return [CropInspectionResult(roi_id=f'{inspected.id}-roi', label=label, bbox=[0, 0, 32, 32], defect_score=0.9 if verdict == 'NG' else 0.1,
                                     confidence=0.95, verdict=verdict, crop_thumbnail='', flaw_type='')], 1.0, status
    monkeypatch.setattr(engine, '_inspect_crops', inspect)
    return engine


def outcome(result):
    return result['final_verdict'], result['routed_output_node_id']


def test_an_image_without_the_class_follows_the_decisions_explicit_rule(monkeypatch):
    engine = engine_seeing(monkeypatch, 'ok')
    unset = engine.execute(pipeline=flow(), image=IMAGE)
    assert outcome(unset) == ('REVIEW', 'review') and 'No active route reached the decision' in unset['rejection_reason'], \
        'a flow without the rule keeps its earlier behaviour'
    explicit = engine.execute(pipeline=flow('review'), image=IMAGE)
    assert outcome(explicit) == ('REVIEW', 'review') and 'no condition toward it was met' in explicit['rejection_reason']
    assert outcome(engine.execute(pipeline=flow('ok'), image=IMAGE)) == ('OK', 'ok')
    assert outcome(engine.execute(pipeline=flow('ng'), image=IMAGE)) == ('NG', 'ng')
    # The branch that is met still decides as before.
    assert outcome(engine_seeing(monkeypatch, 'scratch', verdict='NG').execute(pipeline=flow('ok'), image=IMAGE)) == ('NG', 'ng')


def test_a_later_step_skipped_because_its_condition_was_not_met_is_still_a_known_outcome(monkeypatch):
    result = engine_seeing(monkeypatch, 'ok').execute(pipeline=flow('ok', chain=True), image=IMAGE)
    assert outcome(result) == ('OK', 'ok')
    detail = next(step for step in result['execution_steps'] if step['node_id'] == 'detail')
    assert (detail['status'], detail['skip_reason']) == ('skipped', 'condition_not_met')


def test_an_unknown_never_becomes_ok_through_the_rule(monkeypatch):
    failed = engine_seeing(monkeypatch, 'ok', fail='model').execute(pipeline=flow('ok'), image=IMAGE)
    assert outcome(failed) == ('REVIEW', 'review') and 'too many tiles' in failed['rejection_reason']
    nothing = engine_seeing(monkeypatch, 'ok', empty=True).execute(pipeline=flow('ok'), image=IMAGE)
    assert outcome(nothing) == ('REVIEW', 'review'), 'a model that answered nothing (REVIEW) is not "no scratch"'
    assert 'No active route reached the decision' in nothing['rejection_reason']
    chained = engine_seeing(monkeypatch, 'scratch', fail='detail').execute(pipeline=flow('ok', chain=True), image=IMAGE)
    assert outcome(chained) == ('REVIEW', 'review')
    pipeline = flow('ok')
    pipeline.nodes[2].data.params['incomplete_policy'] = 'ng'
    assert outcome(engine_seeing(monkeypatch, 'ok', fail='model').execute(pipeline=pipeline, image=IMAGE)) == ('NG', 'ng')


def test_the_rule_is_validated_and_saved_with_the_flow():
    pipeline = flow('ok')
    ordered_linear_nodes(pipeline)
    assert FlowchartPipeline.model_validate_json(pipeline.model_dump_json()).nodes[2].data.params == {'no_branch_policy': 'ok'}
    for bad in ('pass', 'OK', '', 1):
        broken = flow()
        broken.nodes[2].data.params['no_branch_policy'] = bad
        with pytest.raises(ValueError, match='no_branch_policy'):
            ordered_linear_nodes(broken)


def test_a_model_that_answered_ng_never_ends_ok_through_the_rule(monkeypatch):
    """The QA's flow: a classifier says 'dent' (NG) and only 'scratch present' leads on. The rule OK does not make it OK."""
    dent = engine_seeing(monkeypatch, 'dent', verdict='NG')
    assert outcome(dent.execute(pipeline=flow('ok'), image=IMAGE)) == ('REVIEW', 'review')
    assert outcome(dent.execute(pipeline=flow('ng'), image=IMAGE)) == ('NG', 'ng')


def test_flows_saved_before_the_rule_keep_their_verdicts(monkeypatch):
    engine = engine_seeing(monkeypatch, 'ok')
    pipeline = flow()
    pipeline.nodes[2].data.params['incomplete_policy'] = 'ng'
    assert outcome(engine.execute(pipeline=pipeline, image=IMAGE)) == ('NG', 'ng'), 'incomplete_policy ng still decides'
    two = flow()
    two.nodes = [node for node in two.nodes if node.id != 'review']
    two.edges = [edge for edge in two.edges if edge.target != 'review']
    two.nodes[2].data.params.update({'incomplete_policy': 'ng', 'review_fallback': 'pass'})
    assert outcome(engine.execute(pipeline=two, image=IMAGE)) == ('NG', 'ng'), 'never routed to the OK output'


def test_a_result_node_behind_an_unmet_condition_is_not_reached_and_the_rule_applies(monkeypatch):
    params = {'no_branch_policy': 'ok'}
    nodes = [node('input', 'input'), node('model', 'inspection', task='classification', model_job_id='job_a'),
             node('summary', 'aggregate', rule='any_ng'), node('decision', 'decision', rule='aggregate_verdict', params=params),
             node('ok', 'output'), node('ng', 'output'), node('review', 'output')]
    edges = [FlowEdge(id='to-model', source='input', target='model'),
             FlowEdge(id='to-summary', source='model', target='summary', predicate=PRESENT),
             FlowEdge(id='to-decision', source='summary', target='decision'),
             FlowEdge(id='pass', source='decision', target='ok', isBranch='pass'),
             FlowEdge(id='fail', source='decision', target='ng', isBranch='fail'),
             FlowEdge(id='review', source='decision', target='review', isBranch='review')]
    pipeline = FlowchartPipeline(nodes=nodes, edges=edges)
    result = engine_seeing(monkeypatch, 'ok').execute(pipeline=pipeline, image=IMAGE)
    assert outcome(result) == ('OK', 'ok'), result['rejection_reason']
    summary = next(step for step in result['execution_steps'] if step['node_id'] == 'summary')
    assert (summary['status'], summary['skip_reason']) == ('skipped', 'condition_not_met')


def test_a_debug_run_and_an_incomplete_image_never_use_the_rule(monkeypatch):
    engine = engine_seeing(monkeypatch, 'ok')
    debug = engine.execute(pipeline=flow('ok'), image=IMAGE, stop_node_id='decision')
    assert debug['final_verdict'] != 'OK'
    failed = engine_seeing(monkeypatch, 'ok', fail='model').execute(pipeline=flow('ok'), image=IMAGE)
    assert outcome(failed) == ('REVIEW', 'review') and 'too many tiles' in failed['rejection_reason']


@pytest.mark.parametrize('missing', ['empty', 'failure', 'review'])
@pytest.mark.parametrize('result_node', [node('summary', 'aggregate', rule='any_ng'),
                                       node('blob', 'blob_measure', params={'min_area_px': 4})])
def test_unknown_upstream_is_not_reported_as_a_known_unmet_condition(monkeypatch, missing, result_node):
    engine = engine_seeing(monkeypatch, 'ok', empty=missing == 'empty',
                           fail='model' if missing == 'failure' else None,
                           status='review_required' if missing == 'review' else 'passed')
    result = engine.execute(pipeline=_behind_two_steps(result_node), image=IMAGE)
    assert outcome(result) == ('REVIEW', 'review')
    steps = {step['node_id']: step for step in result['execution_steps']}
    assert steps['detail']['skip_reason'] == 'upstream_incomplete'
    assert steps['detail']['branch_verdict'] == 'REVIEW'
    assert steps[result_node.id]['skip_reason'] != 'condition_not_met'
    assert 'model' in result['rejection_reason'] and 'incomplete upstream' in result['rejection_reason']


def _behind_two_steps(result_node, policy='ok'):
    """input -> model -[scratch present]-> detail -> <result_node> -> decision (review of freeze 2, P2-1)."""
    params = {'no_branch_policy': policy} if policy else {}
    nodes = [node('input', 'input'), node('model', 'inspection', task='classification', model_job_id='job_a'),
             node('detail', 'inspection', task='segmentation', model_job_id='job_b'), result_node,
             node('decision', 'decision', rule='aggregate_verdict' if result_node.data.node_type == 'aggregate' else 'any_defect_is_ng', params=params),
             node('ok', 'output'), node('ng', 'output'), node('review', 'output')]
    edges = [FlowEdge(id='to-model', source='input', target='model'),
             FlowEdge(id='to-detail', source='model', target='detail', payload_type='image', predicate=PRESENT),
             FlowEdge(id='to-result', source='detail', target=result_node.id),
             FlowEdge(id='to-decision', source=result_node.id, target='decision'),
             FlowEdge(id='pass', source='decision', target='ok', isBranch='pass'),
             FlowEdge(id='fail', source='decision', target='ng', isBranch='fail'),
             FlowEdge(id='review', source='decision', target='review', isBranch='review')]
    return FlowchartPipeline(nodes=nodes, edges=edges)


@pytest.mark.parametrize('result_node', [node('summary', 'aggregate', rule='any_ng'),
                                         node('blob', 'blob_measure', params={'min_area_px': 4})])
def test_a_result_node_two_steps_behind_an_unmet_condition_is_not_reached_either(monkeypatch, result_node):
    engine = engine_seeing(monkeypatch, 'ok')
    result = engine.execute(pipeline=_behind_two_steps(result_node), image=IMAGE)
    assert outcome(result) == ('OK', 'ok'), result['rejection_reason']
    steps = {step['node_id']: step for step in result['execution_steps']}
    assert (steps['detail']['skip_reason'], steps[result_node.id]['skip_reason']) == ('condition_not_met', 'condition_not_met')
    # Unset, the flow keeps its earlier handling: the result node runs on nothing and the image is not judged OK.
    unset = engine.execute(pipeline=_behind_two_steps(result_node, policy=None), image=IMAGE)
    assert unset['final_verdict'] != 'OK'
    assert next(step for step in unset['execution_steps'] if step['node_id'] == result_node.id)['skip_reason'] != 'condition_not_met'


def test_an_ok_rule_declined_because_a_model_said_ng_says_so(monkeypatch):
    """Review of freeze 2 (P3-5): the reason names the NG answer instead of the generic one."""
    result = engine_seeing(monkeypatch, 'ok', verdict='NG').execute(pipeline=flow('ok'), image=IMAGE)
    assert outcome(result) == ('REVIEW', 'review')
    assert 'its rule OK was not used because model answered NG' in result['rejection_reason']
