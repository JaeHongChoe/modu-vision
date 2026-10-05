"""OCR recipe behavior; synthetic geometry proves plumbing, not recognition quality."""
import json
import numpy as np
import pytest
import torch
from PIL import Image, ImageDraw
from backend.engine import ocr


def checkpoint(tmp_path, recipe=None):
    model = ocr.SmallCTCOCR(1)
    with torch.no_grad():
        for parameter in model.parameters(): parameter.zero_()
        model.head.bias[1] = 15
    path = tmp_path / 'best_model.pt'
    payload = {'task':'ocr','version':1,'alphabet':'A','image_size':[32,64],
               'model_state_dict':model.state_dict(),'dataset_provenance':{'dataset_sha256':'fixture'},'best_epoch':1}
    if recipe is not None: payload['recipe'] = recipe
    torch.save(payload, path)
    return path


def scene():
    image = Image.new('RGB',(100,60),'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((30,10,49,17),fill='black')
    draw.rectangle((10,35,29,42),fill='black')
    return np.asarray(image)


def test_persisted_detect_recipe_recognizes_each_original_region_in_reading_order(tmp_path):
    path = checkpoint(tmp_path, {'mode':'detect_recognize'})
    result = ocr.predict_ocr_array(path,scene())
    assert result['text'] == 'A\nA'
    assert [r['box'] for r in result['regions']] == [[30,10,50,18],[10,35,30,43]]
    assert [r['line_index'] for r in result['regions']] == [0,1]
    assert result['coordinate_space'] == 'original_image_pixels'
    assert result['detector']['kind'] == 'classical_horizontal_projection'


def test_blank_image_detects_no_text_and_legacy_crop_still_calls_recognizer(tmp_path):
    blank = np.full((60,100,3),255,dtype=np.uint8)
    result = ocr.predict_ocr_array(checkpoint(tmp_path,{'mode':'detect_recognize'}),blank)
    assert result['text'] == '' and result['regions'] == [] and result['confidence'] == 0
    legacy = ocr.predict_ocr_array(checkpoint(tmp_path),scene())
    assert legacy['text'] == 'A' and legacy['mode'] == 'crop'


def test_recipe_normalizes_unicode_and_reports_text_rule_failures():
    assert hasattr(ocr,'OCRRecipe'), 'OCR recipe contract is missing'
    recipe = ocr.OCRRecipe.from_value({'charset':'가A1','normalizer':'nfkc_strip',
        'text_rules':{'regex':'가A[0-9]','min_length':3,'max_length':3,'allowed_values':['가A1']}})
    assert recipe.normalize(' 가Ａ１ ') == '가A1'
    assert recipe.check_text(' 가Ａ１ ')['passed'] is True
    assert recipe.check_text('가A2')['failed_rules'] == ['allowed_values']
    with pytest.raises(ValueError,match='charset'): recipe.validate_alphabet('Z')
    with pytest.raises(ValueError,match='normalizer'): ocr.OCRRecipe.from_value({'normalizer':'unknown'})


def test_recipe_is_saved_in_real_cpu_training_and_evaluation_reports_wer(tmp_path):
    rows=[]
    for i,split in enumerate(('train','val','test')):
        image=Image.new('L',(32,16),255);image.putpixel((0,0),i)
        image.save(tmp_path/f'{i}.png');rows.append({'image':f'{i}.png','text':'A','split':split})
    ocr.write_ocr_manifest(tmp_path,rows)
    output=tmp_path/'model'
    result=ocr.train_ocr(tmp_path,output,epochs=1,image_size=(16,32),recipe={'mode':'detect_recognize','charset':'A'})
    assert result['recipe']['mode'] == 'detect_recognize'
    assert json.loads((output/'model_meta.json').read_text())['recipe']['charset'] == 'A'
    evaluation=ocr.evaluate_ocr_checkpoint(output/'best_model.pt',tmp_path)
    assert evaluation['mode'] == 'detect_recognize' and evaluation['word_error_rate'] == 1


def test_detect_input_rejects_invalid_shape_and_explicit_unsupported_orientation(tmp_path):
    path=checkpoint(tmp_path,{'mode':'detect_recognize'})
    with pytest.raises(ValueError,match='RGB'):ocr.predict_ocr_array(path,np.zeros((10,10),dtype=np.uint8))
    assert hasattr(ocr,'OCRRecipe'), 'OCR recipe contract is missing'
    with pytest.raises(ValueError,match='horizontal'):ocr.OCRRecipe.from_value({'orientation':'vertical'})


def test_recipe_charset_fails_preflight_before_a_background_job_is_accepted(tmp_path):
    from backend.tests.test_ocr_api import _client,_dataset
    client=_client(tmp_path);client.post('/api/project/create',json={'name':'OCR charset'})
    source=tmp_path/'source';rows=_dataset(source)
    client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    prepared=client.post('/api/ocr/prepare',json={'source_dataset_path':str(source),'samples':rows})
    assert prepared.status_code==200,prepared.text
    result=client.post('/api/ocr/train',json={'dataset_path':prepared.json()['dataset_path'],'epochs':1,'background':True,'recipe':{'charset':'A'}})
    assert result.status_code==422,result.text
    assert 'charset' in result.json()['detail']


# Additional whole-flow recipe evidence must not be omitted from parity.
import copy
import pytest
from backend.engine.ocr_recipe import OCRRecipe
from backend.engine.flow_package_runtime import compare_flow_results

@pytest.mark.parametrize('field,value',[
 ('ocr_regions',[{'box':[1,2,8,9],'polygon':[[1,2],[8,2],[8,9],[1,9]],'line_index':0,'text':'가1'}]),
 ('ocr_recipe',{'mode':'crop','text_rules':{'min_length':3}}),
 ('ocr_rule_result',{'passed':True,'failed_rules':[]}),
])
def test_package_parity_cannot_ignore_ocr_evidence(field,value):
    reference={'crops':[{'defect_score':0.,'ocr_regions':[],'ocr_recipe':{},'ocr_rule_result':{'passed':False,'failed_rules':['regex']}}]}
    packaged=copy.deepcopy(reference);packaged['crops'][0][field]=value
    result=compare_flow_results(reference,packaged)
    assert result['status']=='mismatch'
    assert f'crops[0].{field}' in result['mismatched_fields']

def test_korean_digit_multiline_blank_and_vertical_scope():
    recipe=OCRRecipe.from_value({'mode':'detect_recognize','charset':'가나12','text_rules':{'regex':'[가나12\\n]+','min_length':1,'max_length':10,'allowed_values':['가1','나2']}})
    recipe.validate_alphabet('12가나')
    assert recipe.check_text('가1')['passed']
    assert recipe.check_text('가1\n나2')['failed_rules']==['allowed_values']
    assert recipe.check_text('')['failed_rules']==['regex','min_length','allowed_values']
    with pytest.raises(ValueError,match='outside'):recipe.validate_alphabet('다9')
    with pytest.raises(ValueError,match='vertical'):OCRRecipe.from_value({'orientation':'vertical'})
