"""The routes a first-time user meets (projects, datasets and their versions, the first-run example, labeling and its
model drafts, training and its preparation panel, evaluation, flows, inspections, model comparisons, model operations
and team label review) answer refusals in Korean. The routes swept last answer as "reason. next action."
(docs/product-direction-easy-labeling-agent.md §5.1 direction 5, §5.4 item 2).
A detail that only forwards an exception (``str(exc)``), a catalogued error or a shared constant is not a literal."""
import re
from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[1] / 'api'
ROUTES = ['routes_project.py', 'routes_dataset.py', 'routes_onboarding.py', 'routes_training.py', 'routes_evaluation.py',
          'routes_flowchart.py', 'routes_inspections.py', 'routes_model_comparisons.py',
          'routes_annotation.py', 'routes_dataset_versions.py', 'routes_training_workspace.py',
          'routes_model_operations.py', 'routes_team_data.py', 'routes_label_suggestions.py', 'routes_label_candidates.py']
# English refusals that other code compares verbatim; they move to Korean only together with their readers.
# 'Import a dataset ...': scripts/e2e/dataset-source-empty-controls, portable-abort-recovery, portable-home-refusal,
#   portable-pending-handoff, portable-status-recovery and runtime-pack-picker-cancel compare the response body.
# "Dataset path does not match ...": src/renderer/components/dataset/datasetWorkflowMetadataScope.test.cjs.
PINNED = {'routes_dataset_versions.py': ['Import a dataset into this project before creating a version.',
                                          "Dataset path does not match the active project's source."]}
# Every Korean literal on these routes names the next action; the earlier eight were only moved to Korean.
NEXT_ACTION_ROUTES = ['routes_annotation.py', 'routes_dataset_versions.py', 'routes_training_workspace.py',
                      'routes_model_operations.py', 'routes_team_data.py', 'routes_label_suggestions.py',
                      'routes_label_candidates.py']
LITERAL = re.compile(r"""HTTPException\(\s*(?:status_code=)?\d+\s*,\s*(?:detail=)?\(?\s*f?(['"])(.+?)\1""", re.S)
HANGUL = re.compile('[가-힣]')


def _source(name):
    return (API / name).read_text(encoding='utf-8')


@pytest.mark.parametrize('name', ROUTES)
def test_literal_refusals_on_beginner_routes_are_korean(name):
    english = [text for _, text in LITERAL.findall(_source(name)) if not HANGUL.search(text)]
    assert english == PINNED.get(name, []), f'{name} still answers in English: {english}'


def test_the_duplicate_project_refusal_keeps_the_phrase_the_example_runner_waits_for():
    assert '프로젝트가 이미 있습니다' in _source('routes_project.py')


def test_the_pinned_dataset_version_refusals_stay_verbatim_for_their_readers():
    source = _source('routes_dataset_versions.py')
    for phrase in PINNED['routes_dataset_versions.py']:
        assert phrase in source, f'readers outside the backend compare this text: {phrase!r}'


@pytest.mark.parametrize('name', NEXT_ACTION_ROUTES)
def test_korean_refusals_on_the_swept_routes_name_a_next_action(name):
    korean = [text for _, text in LITERAL.findall(_source(name)) if HANGUL.search(text)]
    assert korean, f'{name} has no literal refusal to check'
    without_action = [text for text in korean if not re.search(r'\.\s', text) or not text.rstrip().endswith('세요.')]
    assert without_action == [], f'{name} refuses without "이유. 다음 행동.": {without_action}'


def test_the_labelme_refusals_keep_the_word_and_the_cause_the_load_failure_test_reads():
    # backend/tests/test_annotation_load_failure.py asserts 'LabelMe' in the detail and reads the forwarded cause.
    labelme = [text for _, text in LITERAL.findall(_source('routes_annotation.py')) if 'LabelMe' in text]
    assert len(labelme) == 2, labelme
    assert all(HANGUL.search(text) and '{e}' in text for text in labelme), labelme


def test_the_restore_refusal_dict_message_is_korean_with_a_next_action():
    # The 409 of /restore answers a dict ({'message', 'changed_files'}) that LITERAL cannot see.
    source = _source('routes_dataset_versions.py')
    assert 'restoration was not started' not in source
    message = re.search(r'"message":\s*"([^"]+)"', source)
    assert message is not None
    assert HANGUL.search(message.group(1)) and message.group(1).endswith('세요.'), message.group(1)
    assert '"changed_files": verification["changed_files"]' in source


def test_the_stale_data_review_refusals_keep_the_phrase_the_adoption_loop_stops_on():
    # src/renderer/components/labeling/draftReview.ts staleBatchRefusal stops a multi-adoption on this opening phrase,
    # and only the review refusal of a changed dataset may carry it (a changed image or model file does not stop the loop).
    for name in ('routes_label_suggestions.py', 'routes_label_candidates.py'):
        stale = [text for _, text in LITERAL.findall(_source(name)) if text.startswith('초안을 만든 뒤 데이터가 바뀌어')]
        assert len(stale) == 1, (name, stale)


def test_the_draft_batch_stop_reasons_are_korean():
    # Not HTTP details, but the batch record shows them as the reason a batch stopped.
    assert 'changed during this batch' not in _source('routes_label_suggestions.py')
    assert 'Source dataset changed during labeling batch' not in _source('routes_label_candidates.py')
