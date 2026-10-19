"""The routes a first-time user meets (projects, datasets, the first-run example, training, evaluation, flows,
inspections and model comparisons) answer refusals in Korean.
A detail that only forwards an exception (``str(exc)``), a catalogued error or a shared constant is not a literal."""
import re
from pathlib import Path

import pytest

ROUTES = ['routes_project.py', 'routes_dataset.py', 'routes_onboarding.py', 'routes_training.py', 'routes_evaluation.py',
          'routes_flowchart.py', 'routes_inspections.py', 'routes_model_comparisons.py']
LITERAL = re.compile(r"""HTTPException\(\s*(?:status_code=)?\d+\s*,\s*(?:detail=)?\(?\s*f?(['"])(.+?)\1""", re.S)
HANGUL = re.compile('[가-힣]')


@pytest.mark.parametrize('name', ROUTES)
def test_literal_refusals_on_beginner_routes_are_korean(name):
    source = (Path(__file__).resolve().parents[1] / 'api' / name).read_text(encoding='utf-8')
    english = [text for _, text in LITERAL.findall(source) if not HANGUL.search(text)]
    assert english == [], f'{name} still answers in English: {english}'


def test_the_duplicate_project_refusal_keeps_the_phrase_the_example_runner_waits_for():
    source = (Path(__file__).resolve().parents[1] / 'api' / 'routes_project.py').read_text(encoding='utf-8')
    assert '프로젝트가 이미 있습니다' in source
