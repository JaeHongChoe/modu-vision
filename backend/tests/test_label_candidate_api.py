import inspect,json
from pathlib import Path
import httpx,numpy as np,pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api import routes_project,routes_label_suggestions,routes_label_candidates,routes_dataset_versions
from backend.engine.annotation_storage import set_request_annotation_root,reset_request_annotation_root,set_request_project_root,reset_request_project_root,dataset_annotation_dir
from backend.engine.dataset_loaders import set_request_split_root,reset_request_split_root

@pytest.fixture
def workspace(tmp_path,monkeypatch):
    if 'app' not in inspect.signature(httpx.Client.__init__).parameters:
        original=httpx.Client.__init__
        def compatible(self,*args,app=None,**kwargs):return original(self,*args,**kwargs)
        monkeypatch.setattr(httpx.Client,'__init__',compatible)
    source=tmp_path/'source';source.mkdir();rng=np.random.default_rng(20);target=rng.integers(0,256,(60,80,3),dtype=np.uint8);patch=rng.integers(0,256,(12,15,3),dtype=np.uint8);target[20:32,30:45]=patch
    Image.fromarray(target).save(source/'target.png');Image.fromarray(patch).save(source/'exemplar.png')
    app=FastAPI();app.state.project_dir=tmp_path/'projects'
    @app.middleware('http')
    async def scope(request,call_next):
        active=getattr(app.state,'current_project',None)
        if not active:return await call_next(request)
        a=set_request_annotation_root(Path(active['annotations_dir']));p=set_request_project_root(Path(active['project_dir']));s=set_request_split_root(Path(active['dataset_dir'])/'splits')
        try:return await call_next(request)
        finally:reset_request_project_root(p);reset_request_annotation_root(a);reset_request_split_root(s)
    for router in [routes_project.router,routes_label_suggestions.router,routes_label_candidates.router,routes_dataset_versions.router]:app.include_router(router)
    client=TestClient(app);project=client.post('/api/project/create',json={'name':'candidate','task':'detection'}).json();client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    return client,project,source

def test_real_template_proposal_review_saves_exact_geometry_and_backup(workspace):
    client,project,source=workspace;before=(source/'target.png').read_bytes()
    created=client.post('/api/label-candidates/generate',json={'backend':'template_match','image_path':str(source/'target.png'),'exemplar_path':str(source/'exemplar.png'),'label':'Scratch','threshold':.99})
    assert created.status_code==200,created.text
    proposal=created.json();assert proposal['status']=='pending';assert proposal['candidates'][0]['annotation']['bbox']==[30.,20.,45.,32.]
    path=dataset_annotation_dir(source,Path(project['annotations_dir']),use_scope=False)/'target.json'
    assert not path.exists()
    adopted=client.post('/api/label-suggestions/'+proposal['id']+'/review',json={'decision':'accept','candidate_ids':[proposal['candidates'][0]['id']],'actor':'Reviewer Kim'})
    assert adopted.status_code==200,adopted.text
    assert adopted.json()['reviewer']=='Reviewer Kim' and adopted.json()['backup_version_id']
    assert json.loads(path.read_text())['annotations'][0]['bbox']==[30.,20.,45.,32.]
    assert (source/'target.png').read_bytes()==before
    assert client.post('/api/label-suggestions/'+proposal['id']+'/review',json={'decision':'reject'}).status_code==409

def test_missing_semantic_model_is_explicit_and_source_changed_blocks_template_review(workspace):
    client,project,source=workspace
    status=client.get('/api/label-candidates/setup').json();assert status['ready'] is False
    no_model=client.post('/api/label-candidates/generate',json={'backend':'grounding_dino','image_path':str(source/'target.png'),'prompt':'scratch.'})
    assert no_model.status_code==422
    proposal=client.post('/api/label-candidates/generate',json={'backend':'template_match','image_path':str(source/'target.png'),'exemplar_path':str(source/'exemplar.png'),'threshold':.99}).json()
    Image.new('RGB',(80,60),'white').save(source/'target.png')
    assert client.post('/api/label-suggestions/'+proposal['id']+'/review',json={'decision':'accept','candidate_ids':[proposal['candidates'][0]['id']]}).status_code==409


def test_a_phrase_draft_is_labelled_with_the_class_it_names_else_the_chosen_class(workspace,monkeypatch,tmp_path):
    """Grounding DINO boxes carry the phrase they matched; a phrase naming a project class ("scratch" → Scratch, "긁힘"
    for a class 긁힘) drafts under that class, any other phrase under the class the person chose. The phrase stays."""
    client,project,source=workspace
    model=tmp_path/'gdino';model.mkdir();(model/'config.json').write_text(json.dumps({'model_type':'grounding-dino'}));(model/'model.safetensors').write_bytes(b'x')
    monkeypatch.setattr(routes_label_candidates,'semantic_readiness',lambda directory:{'ready':True,'error':None,'limits':'test'})
    def boxes(image,directory,prompt,threshold,text_threshold,device='cpu',cancel=None):
        return [{'confidence':.8,'annotation':{'type':'bbox','label':'scratch','category_id':1,'bbox':[30.,20.,45.,32.],'color':'#22d3ee'},
                 'grounding':{'phrase':'scratch','query':'scratch','phrase_score':.8}},
                {'confidence':.6,'annotation':{'type':'bbox','label':'긁힘','category_id':1,'bbox':[2.,2.,12.,12.],'color':'#22d3ee'},
                 'grounding':{'phrase':'긁힘','query':'scratch','phrase_score':.6}},
                {'confidence':.5,'annotation':{'type':'bbox','label':'dent','category_id':1,'bbox':[50.,40.,60.,50.],'color':'#22d3ee'},
                 'grounding':{'phrase':'dent','query':'dent','phrase_score':.5}}]
    monkeypatch.setattr(routes_label_candidates,'grounded_candidates',boxes)
    assert client.put('/api/label-candidates/setup',json={'model_dir':str(model)}).status_code==200
    created=client.post('/api/label-candidates/generate',json={'backend':'grounding_dino','image_path':str(source/'target.png'),'prompt':'scratch, 긁힘, dent',
        'label':'Defect','class_ids':{'Scratch':1,'Defect':2},'output_geometry':'bbox','threshold':.3})
    assert created.status_code==200,created.text
    labels=[(c['annotation']['label'],c['annotation']['category_id'],c['provenance']['grounding_phrase']['phrase']) for c in created.json()['candidates']]
    assert labels==[('Scratch',1,'scratch'),('Scratch',1,'긁힘'),('Defect',2,'dent')]


def test_a_batch_with_a_korean_phrase_the_model_cannot_read_is_refused_before_it_starts(workspace):
    client,project,source=workspace
    refused=client.post('/api/label-candidates/batches',json={'backend':'grounding_dino','image_paths':[str(source/'target.png')],'prompt':'scratch, 작은 긁힘','label':'Scratch'})
    assert refused.status_code==422 and "'작은 긁힘'을(를) 영어로 적으세요" in refused.json()['detail']
    assert client.get('/api/label-candidates/batches').json()['batches']==[]


def test_a_phrase_batch_runs_with_the_projects_own_split_and_one_failed_image_does_not_stop_the_rest(workspace,monkeypatch,tmp_path):
    """The batch thread reads the project's own split: a project with a saved split used to fingerprint another manifest
    in the thread and fail its first image. An image that fails is recorded and the next ones still get drafts."""
    import hashlib,time
    client,project,source=workspace
    Image.new('RGB',(80,60),'gray').save(source/'broken.png')
    key=hashlib.sha256(str(source.resolve()).encode()).hexdigest()
    splits=Path(project['dataset_dir'])/'splits';splits.mkdir(parents=True,exist_ok=True)
    (splits/f'{key}.json').write_text(json.dumps({str(source/'target.png'):'train',str(source/'exemplar.png'):'val',str(source/'broken.png'):'test'}))
    model=tmp_path/'gdino';model.mkdir();(model/'config.json').write_text(json.dumps({'model_type':'grounding-dino'}));(model/'model.safetensors').write_bytes(b'x')
    monkeypatch.setattr(routes_label_candidates,'semantic_readiness',lambda directory:{'ready':True,'error':None,'limits':'test'})
    def boxes(image,directory,prompt,threshold,text_threshold,device='cpu',cancel=None):
        if Path(image).name=='broken.png': raise ValueError('이 이미지는 읽지 못했습니다')
        return [{'confidence':.8,'annotation':{'type':'bbox','label':'scratch','category_id':1,'bbox':[30.,20.,45.,32.],'color':'#22d3ee'},
                 'grounding':{'phrase':'scratch','query':'scratch','phrase_score':.8}}]
    monkeypatch.setattr(routes_label_candidates,'grounded_candidates',boxes)
    assert client.put('/api/label-candidates/setup',json={'model_dir':str(model)}).status_code==200
    started=client.post('/api/label-candidates/batches',json={'backend':'grounding_dino','prompt':'scratch','label':'Scratch','class_ids':{'Scratch':1},
        'output_geometry':'bbox','image_paths':[str(source/'broken.png'),str(source/'target.png'),str(source/'exemplar.png')]})
    assert started.status_code==200,started.text
    assert started.json()['phrases']==[{'text':'scratch','query':'scratch'}]
    for _ in range(200):
        batch=client.get('/api/label-candidates/batches/'+started.json()['id']).json()
        if batch['status'] not in ('queued','running'):break
        time.sleep(.05)
    assert batch['status']=='completed',batch
    assert (batch['processed'],batch['failed'],batch['generated'])==(3,1,2)
    assert [entry['status'] for entry in batch['entries']]==['failed','generated','generated']
    assert batch['entries'][0]['error']=='이 이미지는 읽지 못했습니다'


def test_with_no_class_chosen_an_unmatched_phrase_drafts_under_a_new_class_with_its_own_id(workspace,monkeypatch,tmp_path):
    """An empty project's first drafts name their classes: label '' keeps 'dent' and '찍힘' as typed, each with an id after
    the project's own, while 'scratch' still maps to the project's Scratch."""
    client,project,source=workspace
    model=tmp_path/'gdino';model.mkdir();(model/'config.json').write_text(json.dumps({'model_type':'grounding-dino'}));(model/'model.safetensors').write_bytes(b'x')
    monkeypatch.setattr(routes_label_candidates,'semantic_readiness',lambda directory:{'ready':True,'error':None,'limits':'test'})
    def boxes(image,directory,prompt,threshold,text_threshold,device='cpu',cancel=None):
        return [{'confidence':.9-i/10,'annotation':{'type':'bbox','label':phrase,'category_id':1,'bbox':[2.+20*i,2.,12.+20*i,12.],'color':'#22d3ee'},
                 'grounding':{'phrase':phrase,'query':query,'phrase_score':.5}} for i,(phrase,query) in enumerate([('scratch','scratch'),('dent','dent')])]
    monkeypatch.setattr(routes_label_candidates,'grounded_candidates',boxes)
    assert client.put('/api/label-candidates/setup',json={'model_dir':str(model)}).status_code==200
    created=client.post('/api/label-candidates/generate',json={'backend':'grounding_dino','image_path':str(source/'target.png'),'prompt':'scratch, dent, 찍힘',
        'label':'','class_ids':{'Scratch':2,'Void':4},'output_geometry':'bbox','threshold':.3})
    assert created.status_code==200,created.text
    assert [(c['annotation']['label'],c['annotation']['category_id'],c['provenance'].get('new_class',False)) for c in created.json()['candidates']]==[
        ('Scratch',2,False),('dent',1,True)]
    # The id is the project's, not the request's: the same name keeps it on the next image and after a restart, and a
    # name the project's saved labels already use takes that class.
    again=client.post('/api/label-candidates/generate',json={'backend':'grounding_dino','image_path':str(source/'target.png'),'prompt':'scratch, dent',
        'label':'','class_ids':{'Scratch':2},'output_geometry':'bbox','threshold':.3}).json()
    assert [(c['annotation']['label'],c['annotation']['category_id']) for c in again['candidates']]==[('Scratch',2),('dent',1)]
    registry=json.loads((Path(project['project_dir'])/'label_suggestions'/'new_classes.json').read_text())
    assert registry=={project.get('active_labelset_id','default'):{'dent':1}}
    # '찍힘' reads as 'dent' (grounding_phrases keeps one query per meaning), so it adds no second new class.
    assert client.post('/api/label-candidates/generate',json={'backend':'template_match','image_path':str(source/'target.png'),'exemplar_path':str(source/'exemplar.png'),'label':''}).status_code==422


def test_new_class_ids_skip_every_id_the_project_uses_and_refuse_when_none_is_left(workspace,monkeypatch,tmp_path):
    client,project,source=workspace
    from backend.engine.annotation_storage import dataset_annotation_dir
    studio=dataset_annotation_dir(source,Path(project['annotations_dir']),use_scope=False);studio.mkdir(parents=True,exist_ok=True)
    (studio/'exemplar.json').write_text(json.dumps({'annotations':[{'label':'Crack','category_id':1,'bbox':[1,1,4,4],'type':'bbox'}],'mask_classes':[{'id':3,'name':'Void'}]}))
    model=tmp_path/'gdino';model.mkdir();(model/'config.json').write_text(json.dumps({'model_type':'grounding-dino'}));(model/'model.safetensors').write_bytes(b'x')
    monkeypatch.setattr(routes_label_candidates,'semantic_readiness',lambda directory:{'ready':True,'error':None,'limits':'test'})
    def boxes(image,directory,prompt,threshold,text_threshold,device='cpu',cancel=None):
        return [{'confidence':.8-i/10,'annotation':{'type':'bbox','label':p,'category_id':1,'bbox':[2.+20*i,2.,12.+20*i,12.],'color':'#22d3ee'},
                 'grounding':{'phrase':p,'query':p,'phrase_score':.5}} for i,p in enumerate(['crack','stain'])]
    monkeypatch.setattr(routes_label_candidates,'grounded_candidates',boxes)
    assert client.put('/api/label-candidates/setup',json={'model_dir':str(model)}).status_code==200
    body={'backend':'grounding_dino','image_path':str(source/'target.png'),'prompt':'crack, stain','label':'','output_geometry':'bbox','threshold':.3}
    made=client.post('/api/label-candidates/generate',json={**body,'class_ids':{'Scratch':2}}).json()
    # 'crack' is the saved labels' Crack (id 1); 'stain' is new and skips 1, 2 (listed) and 3 (a saved mask class).
    assert [(c['annotation']['label'],c['annotation']['category_id']) for c in made['candidates']]==[('Crack',1),('stain',4)]
    full=client.post('/api/label-candidates/generate',json={**body,'prompt':'crack, rust','class_ids':{f'c{i}':i for i in range(2,256)}})
    assert full.status_code==422 and '새 클래스 번호' in full.json()['detail']


def test_a_phrase_never_replaces_the_class_a_few_label_classifier_chose(workspace,monkeypatch):
    """Text finds the regions, the classifier names them: with suggestion_model_id the classifier's class (and the
    provenance that credits it) stays, whatever the phrase or the active class."""
    client,project,source=workspace
    from backend.engine import feature_labeling_jobs
    monkeypatch.setattr(feature_labeling_jobs,'load_feature_model',lambda project,model_id:{'checkpoint_sha256':'f'*64})
    def masks(image,setup,**kwargs):
        assert kwargs['prompt']=='scratch' and kwargs['suggestion_model'] is not None
        return [{'confidence':.7,'annotation':{'type':'polygon','label':'Dent','category_id':2,'bbox':[2.,2.,12.,12.],'polygon':[[2,2],[12,2],[12,12]],'color':'#22d3ee'},
                 'provenance':{'grounding_phrase':{'phrase':'scratch','query':'scratch','phrase_score':.6},'suggestion_model_id':'m1',
                               'score_kind':'minimum_mask_iou_and_learned_class_probability'}}]
    monkeypatch.setattr(routes_label_candidates,'foundation_candidates',masks)
    monkeypatch.setattr(routes_label_candidates,'foundation_readiness',lambda setup,device='cpu':{'limits':'test'})
    made=client.post('/api/label-candidates/generate',json={'backend':'foundation','image_path':str(source/'target.png'),'prompt':'scratch','label':'Scratch',
        'class_ids':{'Scratch':1,'Dent':2},'output_geometry':'polygon','suggestion_model_id':'m1','threshold':.3})
    assert made.status_code==200,made.text
    candidate=made.json()['candidates'][0]
    assert (candidate['annotation']['label'],candidate['annotation']['category_id'])==('Dent',2)
    assert candidate['provenance']['suggestion_model_id']=='m1' and 'new_class' not in candidate['provenance']


def test_a_conflict_stops_the_batch_but_one_unreadable_image_does_not(workspace,monkeypatch):
    import time
    from fastapi import HTTPException
    client,project,source=workspace
    Image.new('RGB',(80,60),'gray').save(source/'third.png')
    original=routes_label_candidates._generate_candidates
    def run(single,project,cancel=None,batch_id=None,fail={}):
        name=Path(single.image_path).name
        if name==fail.get('name'): raise HTTPException(fail['status'],detail=fail['detail'])
        return {'id':'suggestion_'+name,'candidates':[{'id':'c'}]}
    for status,detail,expected in [(422,'이미지를 읽지 못했습니다',('completed',3,1,2)),(409,'Project task, source or label set changed during inference',('failed',2,1,1))]:
        monkeypatch.setattr(routes_label_candidates,'_generate_candidates',lambda single,project,cancel=None,batch_id=None:run(single,project,cancel,batch_id,{'name':'exemplar.png','status':status,'detail':detail}))
        started=client.post('/api/label-candidates/batches',json={'backend':'template_match','label':'Scratch','exemplar_path':str(source/'exemplar.png'),
            'image_paths':[str(source/'target.png'),str(source/'exemplar.png'),str(source/'third.png')]})
        assert started.status_code==200,started.text
        for _ in range(200):
            batch=client.get('/api/label-candidates/batches/'+started.json()['id']).json()
            if batch['status'] not in ('queued','running'):break
            time.sleep(.05)
        assert (batch['status'],batch['processed'],batch['failed'],batch['generated'])==expected,batch
        assert detail in batch['error']
    monkeypatch.setattr(routes_label_candidates,'_generate_candidates',original)
