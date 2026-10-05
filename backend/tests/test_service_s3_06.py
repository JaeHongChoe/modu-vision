"""S3-06 exclusion contract; controlled mask provider, not learned-quality evidence."""
import base64
import io
import numpy as np
import pytest
from PIL import Image
from backend.engine import foundation_labeling as engine
from backend.api import routes_label_candidates as routes
from backend.tests.test_label_candidate_api import workspace


def test_exclusion_clips_pixels_before_geometry_size_filter_and_preserves_input(tmp_path,monkeypatch):
    image=tmp_path/'image.png';Image.new('RGB',(30,20)).save(image);original=image.read_bytes()
    mask=np.zeros((20,30),bool);mask[3:12,7:16]=True;before=mask.copy()
    class Provider:
        provenance={'provider':'controlled-mask-boundary','device':'cpu'}
        def predict(self,*args):return mask,.9
    monkeypatch.setattr(engine,'_load_sam2',lambda *args:Provider())
    options=dict(boxes=[[5,2,20,15]],exclude_regions=[[7,3,10,12]],output_geometry='mask',device='cpu')
    candidate=engine.foundation_candidates(image,{},**options)[0]
    assert candidate['annotation']['bbox']==[10.,3.,16.,12.] and candidate['area']==54
    pixels=np.asarray(Image.open(io.BytesIO(base64.b64decode(candidate['annotation']['mask_rle'].split(',')[1]))))[...,3]>0
    expected=before.copy();expected[3:12,7:10]=False;np.testing.assert_array_equal(pixels,expected)
    np.testing.assert_array_equal(mask,before);assert image.read_bytes()==original
    assert candidate['provenance']['exclude_regions']==[[7.,3.,10.,12.]]
    assert candidate['provenance']['exclusion_semantics']=='remove_intersecting_mask_pixels_before_geometry'
    assert engine.foundation_candidates(image,{},**options,min_area=55)==[]
    assert engine.foundation_candidates(image,{},boxes=options['boxes'],exclude_regions=[[0,0,30,20]])==[]


@pytest.mark.parametrize('region',[[0,0,31,20],[10,3,5,8],[0,0,float('inf'),20],[1,2,3]])
def test_invalid_exclusion_refuses_before_model_loading(tmp_path,monkeypatch,region):
    image=tmp_path/'image.png';Image.new('RGB',(30,20)).save(image)
    def unavailable(*args):pytest.fail('invalid exclusion reached provider loading')
    monkeypatch.setattr(engine,'_load_sam2',unavailable)
    with pytest.raises(ValueError):engine.foundation_candidates(image,{},boxes=[[0,0,20,20]],exclude_regions=[region])


def test_positive_point_in_exclusion_refuses_before_model_loading(tmp_path,monkeypatch):
    image=tmp_path/'image.png';Image.new('RGB',(30,20)).save(image)
    monkeypatch.setattr(engine,'_load_sam2',lambda *args:pytest.fail('contradictory prompt reached provider'))
    with pytest.raises(ValueError,match='positive point'):engine.foundation_candidates(image,{},points=[{'x':8,'y':4,'label':1}],exclude_regions=[[7,3,10,12]])


def test_api_retains_exclusion_spec_and_requires_review_before_original_labels_change(workspace,monkeypatch):
    client,project,source=workspace;image=source/'target.png';before=image.read_bytes();seen=[]
    def infer(path,setup,**kwargs):
        seen.append(kwargs);mask=np.zeros((60,80),bool);mask[20:30,35:40]=True
        return [engine.mask_candidate(mask,.9,'Scratch','mask',{'provider':'controlled-mask-boundary','device':'cpu','exclude_regions':kwargs['exclude_regions']})]
    monkeypatch.setattr(routes,'foundation_candidates',infer)
    response=client.post('/api/label-candidates/generate',json={'backend':'foundation','image_path':str(image),'boxes':[[30,20,40,30]],'exclude_regions':[[30,20,35,30]],'output_geometry':'mask'})
    assert response.status_code==200,response.text;p=response.json();assert p['status']=='pending'
    assert seen[0]['exclude_regions']==[[30,20,35,30]] and p['exclude_regions']==[[30,20,35,30]]
    assert p['prompt_spec']['boxes']==[[30,20,40,30]] and image.read_bytes()==before
    read=client.get('/api/label-suggestions',params={'image_path':str(image)});assert read.status_code==200 and read.json()['suggestions'][0]['exclude_regions']==p['exclude_regions']


def test_other_provider_rejects_exclusion_instead_of_ignoring_it(workspace,monkeypatch):
    client,project,source=workspace
    monkeypatch.setattr(routes,'template_candidates',lambda *args:pytest.fail('unsupported exclusion executed'))
    response=client.post('/api/label-candidates/generate',json={'backend':'template_match','image_path':str(source/'target.png'),'exemplar_path':str(source/'exemplar.png'),'exclude_regions':[[0,0,10,10]]})
    assert response.status_code==422 and 'foundation' in response.json()['detail']
