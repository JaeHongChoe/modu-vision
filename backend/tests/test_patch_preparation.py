import json
from PIL import Image


def test_patch_preparation_uses_manual_regions_and_preserves_sources(tmp_path):
    from backend.engine.patch_preparation import prepare_patch_dataset
    from backend.engine.patch_classification import load_patch_manifest
    source=tmp_path/'source'
    source.mkdir()
    assignments={}
    original={}
    for i,split in enumerate(('train','val','test')):
        path=source/f'{i}.png'
        Image.new('RGB',(32,32),color=(i,10,20)).save(path)
        annotations={'imagePath':path.name,'imageWidth':32,'imageHeight':32,
            'shapes':[{'label':'chip','shape_type':'rectangle','points':[[0,0],[8,8]]}]}
        path.with_suffix('.json').write_text(json.dumps(annotations))
        assignments[str(path)]=split
        original[path.name]=path.read_bytes()
    result=prepare_patch_dataset(source,tmp_path/'prepared',patch_size=16,stride=16,
        assignments=assignments,normal_class='OK',minimum_overlap=.05)
    manifest=load_patch_manifest(result['dataset_path'])
    assert manifest.classes==['OK','chip']
    assert len(manifest.patches)==12
    assert {p.label for p in manifest.patches}=={'OK','chip'}
    assert manifest.provenance['split_counts']=={'train':4,'val':4,'test':4}
    raw=json.loads((tmp_path/'prepared'/'patches.json').read_text())
    assert raw['source_dataset_path']==str(source.resolve())
    assert raw['test_image_verdicts']=={'images/2.png':'NG'}
    assert all((source/name).read_bytes()==data for name,data in original.items())
    assert not (source/'patches.json').exists()


def test_unlabeled_sources_are_not_invented_as_normal_patch_truth(tmp_path):
    from backend.engine.patch_preparation import prepare_patch_dataset
    source=tmp_path/'source'
    source.mkdir()
    Image.new('RGB',(32,32)).save(source/'unknown.png')
    import pytest
    with pytest.raises(ValueError,match='annotated'):
        prepare_patch_dataset(source,tmp_path/'prepared',patch_size=16,stride=16)
