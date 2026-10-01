"""Missing requirements and premature acceptance must fail the delivery gate."""
import importlib.util
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('upgrade_gate',ROOT/'scripts'/'verify_product_upgrade.py')
gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)


def test_gate_rejects_an_omitted_requirement_and_duplicate():
    program=json.loads((ROOT/'docs'/'product-upgrade-program.json').read_text())
    program['requirements'][-1]=program['requirements'][0].copy()
    errors=gate.validate_program(program,ROOT)
    assert any('coverage' in e for e in errors)
    assert any('duplicated' in e for e in errors)


def test_gate_rejects_accepted_feature_without_all_workflow_evidence(tmp_path):
    (tmp_path/'implementation.py').write_text('x=1')
    (tmp_path/'evidence.json').write_text('{}')
    program=json.loads((ROOT/'docs'/'product-upgrade-program.json').read_text())
    for row in program['requirements']:
        row.update(status='planned',implementation=[],evidence=[])
    program['requirements'][0].update(status='accepted',implementation=['implementation.py'],evidence=['evidence.json'])
    assert any('accepted requires' in e for e in gate.validate_program(program,tmp_path))
    program['requirements'][0]['acceptance']={k:'passed' for k in ('gui','persist','reopen','failure','handoff')}
    assert gate.validate_program(program,tmp_path)==[]
