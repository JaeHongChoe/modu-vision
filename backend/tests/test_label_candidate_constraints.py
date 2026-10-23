import numpy as np
import pytest
from PIL import Image
from backend.engine.label_candidate_providers import filter_keywords,template_candidates,semantic_readiness,grounded_candidates


def test_keywords_filter_actual_predictions_case_insensitive():
    candidates=[{'annotation':{'label':'Scratch'}},{'annotation':{'label':'Crack'}}]
    assert filter_keywords(candidates,['scratch'])==[candidates[0]]
    with pytest.raises(ValueError): filter_keywords(candidates,[''])

def test_template_matching_returns_real_source_box_and_rejects_constant_exemplar(tmp_path):
    rng=np.random.default_rng(12); image=rng.integers(0,256,(80,100,3),dtype=np.uint8)
    patch=rng.integers(0,256,(12,15,3),dtype=np.uint8); image[22:34,30:45]=patch
    Image.fromarray(image).save(tmp_path/'image.png');Image.fromarray(patch).save(tmp_path/'patch.png')
    candidates=template_candidates(tmp_path/'image.png',tmp_path/'patch.png','scratch',.99,3)
    assert candidates[0]['annotation']['bbox']==[30.,22.,45.,34.]
    assert candidates[0]['confidence']>.99
    Image.new('RGB',(12,15),'white').save(tmp_path/'empty.png')
    with pytest.raises(ValueError): template_candidates(tmp_path/'image.png',tmp_path/'empty.png','scratch',.5,3)

def test_semantic_provider_never_fabricates_without_local_model(tmp_path):
    ready=semantic_readiness(tmp_path/'missing')
    assert ready['ready'] is False
    assert ready['backend']=='grounding_dino'
    Image.new('RGB',(30,30)).save(tmp_path/'image.png')
    with pytest.raises(ValueError): grounded_candidates(tmp_path/'image.png',tmp_path/'missing','scratch',.2,.2)


def test_grounding_phrases_are_the_models_dot_separated_english_queries():
    """Grounding DINO reads "a. b."; the real model returned no box for "scratch, dent" or a phrase without its '.',
    and its English tokenizer cannot read Korean. Phrases split on commas, periods, semicolons, slashes and lines."""
    from backend.engine.label_candidate_providers import grounding_phrases
    assert grounding_phrases('Scratch, dent\nDark  Square.; scratch') == [
        {'text': 'Scratch', 'query': 'scratch'}, {'text': 'dent', 'query': 'dent'}, {'text': 'Dark Square', 'query': 'dark square'}]
    assert grounding_phrases('긁힘 / 찍힘, 흠집') == [{'text': '긁힘', 'query': 'scratch'}, {'text': '찍힘', 'query': 'dent'}]
    with pytest.raises(ValueError, match="'작은 긁힘'을\\(를\\) 영어로 적으세요"):
        grounding_phrases('scratch, 작은 긁힘')
    with pytest.raises(ValueError, match='nonempty'):
        grounding_phrases(' , . ')


def test_each_phrase_owns_only_its_own_tokens_in_the_merged_text():
    from backend.engine.label_candidate_providers import _phrase_token_spans

    class Tokenizer:  # word pieces with character offsets, like a fast BERT tokenizer, plus [CLS]/[SEP]
        def __call__(self, text, return_offsets_mapping, add_special_tokens):
            import re
            return {'offset_mapping': [(0, 0)] + [m.span() for m in re.finditer(r'\w+|\.', text)] + [(0, 0)]}
    phrases = [{'query': 'dark square'}, {'query': 'dent'}]
    text = 'dark square. dent.'
    assert _phrase_token_spans(Tokenizer(), text, phrases) == [[1, 2], [4]]
