"""An omitted, skipped, or failed S7-04 control can never close the matrix."""
import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

spec=importlib.util.spec_from_file_location('failure_matrix',Path(__file__).parents[2]/'scripts/acceptance/failure_matrix.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


def receipt(tmp_path,damaged=None,state=None):
    root=ET.Element('testsuites'); suite=ET.SubElement(root,'testsuite')
    for cases in module.SCENARIOS.values():
        for name,test in cases:
            if (name,test)==damaged and state=='missing': continue
            case=ET.SubElement(suite,'testcase',classname='backend.tests.'+name,name=test)
            if (name,test)==damaged and state in {'failure','error','skipped'}: ET.SubElement(case,state)
    file=tmp_path/'junit.xml'; ET.ElementTree(root).write(file); return file


def test_all_named_controls_exist_and_actual_xml_is_required(tmp_path):
    selectors=module.selectors()
    assert len(selectors)>=20 and len(selectors)==len(set(selectors))
    result=module.summarize(receipt(tmp_path))
    assert len(result)==10 and all(r['state']=='local_controls_verified' for r in result.values())
    assert all(r['target_execution_verified'] is False for r in result.values())


def test_one_skipped_missing_or_failed_case_leaves_its_scenario_pending(tmp_path):
    scenario=next(iter(module.SCENARIOS)); control=module.SCENARIOS[scenario][0]
    for state in ('missing','failure','error','skipped'):
        result=module.summarize(receipt(tmp_path,control,state))
        assert result[scenario]['state']=='pending'
        assert any(r['state']=='local_controls_verified' for name,r in result.items() if name!=scenario)
