import pytest


def test_preferences_persist_colors_flags_and_reject_stale_updates(tmp_path):
    from backend.engine.project_preferences import read_preferences,update_preferences,PreferenceConflict
    assert read_preferences(tmp_path)['revision']==0
    row=update_preferences(tmp_path,expected_revision=0,actor='reviewer',
        changes={'tag_colors':{'lot-A':'#24A7CB'},'model_flags':{'job_demo':['best','important']},
                 'labelset_flags':{'default':['important']}})
    assert row['revision']==1
    restored=read_preferences(tmp_path)
    assert restored['tag_colors']['lot-A']=='#24A7CB'
    assert restored['model_flags']['job_demo']==['best','important']
    with pytest.raises(PreferenceConflict):
        update_preferences(tmp_path,expected_revision=0,actor='other',changes={'tag_colors':{}})
    assert read_preferences(tmp_path)==restored


def test_preferences_reject_invalid_colors_and_unknown_fields(tmp_path):
    from backend.engine.project_preferences import update_preferences
    for changes in ({'tag_colors':{'bad':'url(secret)'}},{'privileged':True},
                    {'model_flags':{'../../escape':['best']}}):
        with pytest.raises(ValueError):
            update_preferences(tmp_path,expected_revision=0,actor='reviewer',changes=changes)
