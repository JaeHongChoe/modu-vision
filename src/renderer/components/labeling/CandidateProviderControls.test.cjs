// Stage 2 (docs/product-direction-easy-labeling-agent.md §3.3 ③, §8): the SAM2 preparation card follows step 3's weights card —
// a Korean state and cause with one next action, the loader's file list, the weight file's SHA256 as identification only, the
// official page the engine documents name, a typed folder path for the browser — and the reason sits beside the disabled
// generate button. A ready card is one collapsed line, and a weight file the loader would refuse (empty, git-lfs pointer,
// truncated) is 파일 손상 before any click. The card never downloads a model and names no size or licence of its own.
const test=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const Module=require('node:module');const ts=require('typescript');
const directory=__dirname;
const transpile=(file,jsx)=>ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,...(jsx?{jsx:ts.JsxEmit.React,esModuleInterop:true}:{})}}).outputText;
function compile(file,mocks,jsx){const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const original=m.require.bind(m);m.require=name=>Object.hasOwn(mocks,name)?mocks[name]:original(name);m._compile(transpile(file,jsx),file);return m.exports;}
const foundationRequest=compile(path.join(directory,'foundationRequest.ts'),{},false);

const files=(present,bytes)=>[{name:'config.json',present:present[0],bytes:present[0]?bytes[0]:null},{name:'preprocessor_config.json',present:present[1],bytes:present[1]?bytes[1]:null},{name:'model.safetensors',present:present[2],bytes:present[2]?bytes[2]:null}];
const official={family:'SAM 2.1 (Hugging Face transformers format)',example_repo_id:'facebook/sam2.1-hiera-tiny',url:'https://huggingface.co/facebook/sam2.1-hiera-tiny'};
const readiness=(state,extra={})=>({ready:state==='ready',error:state==='ready'?null:'SAM2 model configuration is missing or invalid.',limits:'',dependency_available:state!=='dependency_missing',
  state,expected_files:files([false,false,false],[]),missing_files:['config.json','preprocessor_config.json','model.safetensors'],model_type:null,model_sha256:null,official_reference:official,...extra});
const flattenOf=tree=>!tree||typeof tree!=='object'?[]:[tree,...(tree.props?.children||[]).flat(Infinity).flatMap(flattenOf)];
const setupWith=(foundation,configuration={})=>({configuration,providers:{foundation,grounding_dino:{ready:false,error:'Choose a local Grounding DINO model directory',limits:'',dependency_available:true}},labelset_id:'default',labelset_version:'v'});

// The component rendered one level deep with controlled hooks (what scripts/verify-foundation-labeling-ui.cjs does): the tree
// is inspected, buttons are clicked through their props, and every provider call is recorded.
function mount({setup,canPick=true,currentImage={file_path:'/source-a/a.png'},disabled=false}){
  const slots=[],effects=[];let cursor=0;
  const react={
    createElement:(type,props,...children)=>({type,props:{...(props||{}),children}}),
    useState(initial){const i=cursor++;if(!(i in slots))slots[i]=initial;return[slots[i],value=>{slots[i]=typeof value==='function'?value(slots[i]):value;}];},
    useId(){cursor++;return ':sam2-reason:';},
    useEffect(fn,deps){const i=cursor++;const previous=slots[i];if(!previous||deps.some((value,n)=>value!==previous.deps[n])){previous?.cleanup?.();const entry={deps};slots[i]=entry;effects.push(()=>{entry.cleanup=fn();});}},
  };
  react.default=react;
  const project={projectDir:'/project',task:'segmentation',project:{id:'own',task:'segmentation',active_labelset_id:'default',source_dataset_dir:'/source-a'}};
  const store=state=>Object.assign(selector=>selector?selector(state):state,{getState:()=>state});
  const calls={setup:0,configure:[],opened:[]};
  let current=setup;
  const providers={
    setup:async()=>{calls.setup+=1;return current;},
    configure:async body=>{calls.configure.push(body);if(/\/missing$/.test(String(body.mask_model_dir)))throw {detail:'Choose an existing local model directory'};current={...current,configuration:{...current.configuration,...body}};return current;},
    featureModels:async()=>({models:[]}),batches:async()=>({batches:[]}),featureJobs:async()=>({jobs:[]}),
  };
  const mocks={
    react,
    '../../stores/useProjectStore':{useProjectStore:store(project)},
    '../../stores/useDatasetStore':{useDatasetStore:store({images:[],folderPath:'/source-a'})},
    '../../stores/useAnnotationStore':{useAnnotationStore:store({currentImage,annotations:[],selectedAnnotationId:null,setActiveTool(){},activeCategory:{id:1,name:'scratch'},categories:[]})},
    '../../stores/useModelAssistRunStore':{useModelAssistRunStore:store({begin(){},end(){}})},
    '../../stores/useFoundationPromptStore':{useFoundationPromptStore:store({points:[],boxes:[],pointLabel:1,setPointLabel(){},clear(){},bind(){},bindProject(){}})},
    '../../services/hostAdapter':{host:{can:capability=>capability==='pickPaths'&&canPick,selectFolder:async()=>{throw new Error('No native dialog in this test');},openLink:async url=>{calls.opened.push(url);return true;}}},
    '../../services/api':{api:{dataset:{}}},
    '../../services/datasetWorkflow':{workflowError:error=>error instanceof Error?error.message:typeof error?.detail==='string'?error.detail:String(error)},
    '../../services/foundationLabelingApi':{foundationLabelingApi:providers,labelingJobActive:status=>['queued','running','cancelling'].includes(status)},
    './foundationRequest':foundationRequest,
  };
  const exports=compile(path.join(directory,'CandidateProviderControls.tsx'),mocks,true);
  global.localStorage={getItem:()=>null,setItem(){}};
  global.window={setInterval:()=>1,clearInterval(){}};
  const flatten=tree=>!tree||typeof tree!=='object'?[]:[tree,...(tree.props?.children||[]).flat(Infinity).flatMap(flatten)];
  const text=node=>Array.isArray(node)?node.map(text).join(''):node&&typeof node==='object'?text(node.props.children):node==null||node===false?'':String(node);
  const settle=()=>new Promise(resolve=>setImmediate(resolve));
  let tree=null;
  const render=()=>{cursor=0;tree=exports.CandidateProviderControls({disabled,onCreated(){},onOpenProposal:async()=>{}});effects.splice(0).forEach(fn=>fn());return tree;};
  const nodes=()=>flatten(tree);
  const button=name=>nodes().find(node=>node.type==='button'&&text(node)===name);
  const byLabel=label=>nodes().find(node=>node.props?.['aria-label']===label);
  const card=()=>byLabel('SAM2 모델 준비');
  return {exports,calls,render,settle,text,nodes,button,byLabel,card,
    cardText:()=>text(card()),
    status:()=>text(flatten(card()).find(node=>node.props?.role==='status')),
    details:()=>flatten(card()).find(node=>node.type==='details'),
    summary:()=>text(flatten(card()).find(node=>node.type==='summary')),
    primary:()=>flatten(card()).filter(node=>node.type==='button'&&/\bbg-indigo-700\b/.test(node.props.className)).map(node=>text(node)),
    generateButton:()=>button('현재 이미지 SAM2 후보 생성'),
    reason:()=>{const described=button('현재 이미지 SAM2 후보 생성')?.props['aria-describedby'];return described?text(nodes().find(node=>node.props?.id===described)):null;},
    async open(){render();await settle();render();},
  };
}

test('the card names each readiness state in Korean with a cause and one next action, and the disabled generate button carries the reason',async()=>{
  const cases=[
    ['dependency_missing','설치 필요',/transformers 라벨링 의존성이 없습니다\. requirements-semantic-labeling\.txt를 설치한 뒤/,['파일 확인']],
    ['not_configured','파일 없음',/폴더를 아직 지정하지 않았습니다\. 공식 페이지에서 받은 파일이 있는 폴더를 선택하세요\./,['SAM2 폴더 선택']],
    ['path_invalid','경로 오류',/폴더가 아니거나 읽을 수 없습니다\. 폴더를 다시 선택하세요\./,['SAM2 폴더 선택']],
    ['files_missing','파일 없음',/SAM2 파일이 없습니다\. 아래 목록의 파일을 이 폴더에 두세요\./,['공식 모델 페이지 열기']],
    ['files_incomplete','파일 불완전',/없는 파일: preprocessor_config\.json, model\.safetensors\. 공식 페이지에서/,['공식 모델 페이지 열기']],
    ['invalid_config','파일 불완전',/config\.json을 읽을 수 없습니다\. 공식 페이지의 config\.json으로 바꾸세요\./,['공식 모델 페이지 열기']],
    ['wrong_model_type','파일 불완전',/model_type이 grounding-dino이라 SAM2 모델이 아닙니다\. SAM2 모델 폴더를 선택하세요\./,['SAM2 폴더 선택']],
    ['device_unavailable','장치 없음',/실행 장치를 쓸 수 없습니다\. CPU 또는 사용 가능한 GPU를 고르세요\./,[]],
  ];
  for(const [state,label,cause,primary] of cases){
    const extra=state==='files_incomplete'?{expected_files:files([true,false,false],[812]),missing_files:['preprocessor_config.json','model.safetensors']}:state==='wrong_model_type'?{model_type:'grounding-dino'}:{};
    const ui=mount({setup:setupWith(readiness(state,extra))});await ui.open();
    assert.equal(ui.status(),`SAM2 모델 준비 · ${label}`,state);
    assert.equal(ui.details().props.open,true,`${state}: a blocker keeps the card open`);
    assert.match(ui.cardText(),cause,state);
    assert.deepEqual(ui.primary(),primary,`${state}: only the button the cause names is primary`);
    assert.equal(ui.generateButton().props.disabled,true,state);
    // The reason beside the button carries the card's own cause, so each state names its own next step.
    assert.equal(ui.reason(),`SAM2 모델이 준비되지 않았습니다(${label}). ${ui.exports.sam2ReadinessView(readiness(state,extra)).cause}`,state);
    assert.match(ui.reason(),cause,state);
  }
  const browser=mount({setup:setupWith(readiness('not_configured')),canPick:false});await browser.open();
  assert.deepEqual(browser.primary(),['경로 저장'],'the browser\'s folder action is the typed path');
});

test('a ready folder is one collapsed line; inside it, the file list with measured sizes, the SHA256 as identification only, and generating needs no reason',async()=>{
  const sha='48c14467e5cf9e51870511feb72c89688e82dd74523142c0538b663e193ac2a7';
  const ready=readiness('ready',{expected_files:files([true,true,true],[812,520,155*1048576+12]),missing_files:[],model_type:'sam2',weights_check:'ok',model_sha256:sha});
  const ui=mount({setup:setupWith(ready,{mask_model_dir:'/models/sam2.1-hiera-tiny'})});await ui.open();
  assert.equal(ui.status(),'SAM2 모델 준비 · 준비됨');
  // Step 3's '준비됨을 한 줄로': the summary is the status alone and the rest stays folded, with the folder still changeable inside.
  assert.equal(ui.details().props.open,false,'the ready card is collapsed');
  assert.equal(ui.summary(),'SAM2 모델 준비 · 준비됨');
  assert.ok(flattenOf(ui.details()).some(node=>node.type==='summary'&&node.props.children.flat().some(child=>child?.props?.role==='status')),'the status is the summary');
  assert.ok(ui.button('SAM2 폴더 선택')&&ui.byLabel('SAM2 모델 폴더 경로'),'the folder can still be changed');
  assert.deepEqual(ui.primary(),[],'no card button competes with the generate button');
  assert.match(ui.cardText(),/SAM2 모델 파일을 찾았습니다\. 캔버스에 점·박스를 찍고 후보를 생성하세요\./);
  assert.doesNotMatch(ui.cardText(),/읽을 수 있습니다/,'nothing has opened the weights, so the cause does not say they are readable');
  const items=ui.nodes().filter(node=>node.type==='li'&&/\.json|\.safetensors/.test(ui.text(node))).map(node=>ui.text(node));
  assert.deepEqual(items,['✓ config.json · 812B','✓ preprocessor_config.json · 520B','✓ model.safetensors · 155.0MB'],'below 1KB the bytes themselves');
  const identification=ui.text(ui.byLabel('SAM2 파일 식별 정보'));
  assert.match(identification,new RegExp('model\\.safetensors · SHA256: '+sha));
  assert.match(identification,/파일 내용의 식별값입니다\. 제공처 진위·모델 구조·품질 승인을 뜻하지 않습니다\./);
  assert.equal(ui.byLabel('SAM2 모델 폴더 경로').props.value,'/models/sam2.1-hiera-tiny');
  assert.equal(ui.generateButton().props.disabled,false);
  assert.equal(ui.reason(),null);
  assert.equal(ui.generateButton().props['aria-describedby'],undefined);
});

test('the card offers no download and states no size or licence of its own; the official page opens through the host',async()=>{
  const ui=mount({setup:setupWith(readiness('not_configured'))});await ui.open();
  const text=ui.cardText();
  assert.match(text,/크기·라이선스는 공식 페이지에서 확인하세요\. 앱은 모델 파일을 내려받지 않습니다\./);
  assert.doesNotMatch(text,/MB|GB|Apache|MIT|라이선스:/);
  const buttons=ui.nodes().filter(node=>node.type==='button').map(node=>ui.text(node));
  assert.ok(!buttons.some(name=>/내려받기|다운로드|download/i.test(name)),buttons.join(' | '));
  await ui.button('공식 모델 페이지 열기').props.onClick();
  assert.deepEqual(ui.calls.opened,['https://huggingface.co/facebook/sam2.1-hiera-tiny']);
  const items=ui.nodes().filter(node=>node.type==='li'&&/\.json|\.safetensors/.test(ui.text(node))).map(node=>ui.text(node));
  assert.deepEqual(items,['✗ config.json','✗ preprocessor_config.json','✗ model.safetensors'],'the engine file list is shown before a folder exists, without sizes');
});

test('without a recorded official URL the card names the family and where to look instead of guessing a link',async()=>{
  const ui=mount({setup:setupWith(readiness('not_configured',{official_reference:{family:'SAM 2.1 (Hugging Face transformers format)',url:null}}))});await ui.open();
  assert.equal(ui.button('공식 모델 페이지 열기'),undefined);
  assert.match(ui.cardText(),/SAM 2\.1 \(Hugging Face transformers format\) · 공식 배포처의 모델 페이지에서 파일을 받으세요\./);
});

test('a ready folder without preprocessor_config.json stays ready (the gate is unchanged) and is warned about',async()=>{
  const ready=readiness('ready',{expected_files:files([true,false,true],[812,0,4096]),missing_files:['preprocessor_config.json'],model_type:'sam2',model_sha256:'ab'.repeat(32)});
  const ui=mount({setup:setupWith(ready)});await ui.open();
  assert.equal(ui.status(),'SAM2 모델 준비 · 준비됨');
  assert.equal(ui.details().props.open,true,'a warning keeps the ready card open');
  assert.match(ui.cardText(),/preprocessor_config\.json이 없어 후보를 생성할 때 모델을 불러오지 못합니다\. 공식 페이지에서 이 파일을 받아 같은 폴더에 두세요\./);
  assert.equal(ui.generateButton().props.disabled,false);
});

test('파일 확인 re-reads readiness; the browser has the typed path instead of the folder dialog, and a refused path gets a Korean reason',async()=>{
  const ui=mount({setup:setupWith(readiness('not_configured')),canPick:false});await ui.open();
  assert.equal(ui.calls.setup,1);
  assert.equal(ui.button('SAM2 폴더 선택'),undefined,'no native dialog button in the browser');
  assert.equal(ui.button('경로 저장').props.disabled,true,'nothing typed yet');
  await ui.button('파일 확인').props.onClick();await ui.settle();ui.render();
  assert.equal(ui.calls.setup,2);
  ui.byLabel('SAM2 모델 폴더 경로').props.onChange({target:{value:'  /srv/models/sam2  '}});ui.render();
  assert.equal(ui.button('경로 저장').props.disabled,false);
  await ui.button('경로 저장').props.onClick();await ui.settle();ui.render();
  assert.deepEqual(ui.calls.configure,[{mask_model_dir:'/srv/models/sam2'}],'only the existing setup PUT, with the trimmed path');
  assert.equal(ui.byLabel('SAM2 모델 폴더 경로').props.value,'/srv/models/sam2');
  ui.byLabel('SAM2 모델 폴더 경로').props.onChange({target:{value:'/srv/missing'}});ui.render();
  await ui.button('경로 저장').props.onClick();await ui.settle();ui.render();
  const alert=ui.nodes().find(node=>node.props?.role==='alert');
  assert.equal(ui.text(alert),'지정한 경로를 폴더로 찾지 못했습니다. 백엔드가 읽을 수 있는 폴더 경로를 입력하세요.');
  const desktop=mount({setup:setupWith(readiness('not_configured')),canPick:true});await desktop.open();
  assert.ok(desktop.button('SAM2 폴더 선택'),'the desktop keeps the folder dialog');
});

test('an older readiness without state still renders from ready/error, and the pure helpers order the reasons',async()=>{
  const ui=mount({setup:setupWith({ready:false,error:'Configure mask_model_dir with a local SAM2 model (for example facebook/sam2.1-hiera-tiny).',limits:'',dependency_available:true})});await ui.open();
  assert.equal(ui.status(),'SAM2 모델 준비 · 파일 없음');
  assert.match(ui.cardText(),/SAM2 모델 폴더가 지정되지 않았거나 없습니다\. 공식 페이지에서 받은 파일이 있는 폴더를 선택하세요\./);
  const {sam2ReadinessView,generateBlockedReason}=ui.exports;
  const legacy=error=>{const view=sam2ReadinessView({ready:false,error,limits:'',dependency_available:true});return [view.label,view.action,view.cause];};
  assert.deepEqual(legacy('SAM2 requires the optional transformers>=4.57.6 labeling dependencies in this backend environment.').slice(0,2),['설치 필요','verify'],'an old dependency error is not a missing folder');
  assert.match(legacy('SAM2 requires the optional transformers>=4.57.6 labeling dependencies in this backend environment.')[2],/requirements-semantic-labeling\.txt를 설치한 뒤 파일 확인을 누르세요\./);
  assert.deepEqual(legacy('SAM2 config.json and model.safetensors are required; download official weights before use.').slice(0,2),['파일 불완전','official']);
  assert.deepEqual(legacy('SAM2 model configuration is missing or invalid.').slice(0,2),['파일 불완전','official']);
  assert.deepEqual(legacy('Device cuda:3 is unavailable. Select CPU or an available GPU.').slice(0,2),['장치 없음',null]);
  assert.deepEqual(legacy('Something new.'),['파일 없음','folder','Something new. 모델 폴더를 선택하세요.'],'an unknown old error keeps its text and the folder action');
  assert.equal(sam2ReadinessView({ready:true,error:null,expected_files:files([true,true,true],[812,520,134])}).state,'ready','an older backend without weights_check is not called damaged');
  assert.deepEqual([sam2ReadinessView(null).state,sam2ReadinessView(null).label],['checking','확인 중'],'not read yet is not a file verdict');
  assert.equal(sam2ReadinessView({ready:true,error:null}).state,'ready');
  const ready=setupWith(readiness('ready'));
  const base={setup:ready,prompt:'',currentScope:true,disabled:false,busy:false,running:false,hasImage:true,label:'scratch'};
  assert.equal(generateBlockedReason(base,'image'),null);
  assert.equal(generateBlockedReason(base,'batch',3),null);
  assert.equal(generateBlockedReason({...base,prompt:'scratch.'},'image'),'대상 설명을 쓰려면 Grounding DINO 텍스트 모델도 필요합니다. 설명을 비우거나 텍스트 모델 폴더를 선택하세요.');
  assert.equal(generateBlockedReason({...base,disabled:true},'image'),'저장하지 않은 라벨이 있거나 다른 작업이 진행 중입니다. 저장이 끝난 뒤 다시 시도하세요.');
  assert.equal(generateBlockedReason({...base,running:true},'image'),'요청이 진행 중입니다. 끝난 뒤 다시 시도하세요.');
  assert.equal(generateBlockedReason({...base,hasImage:false},'image'),'열린 이미지가 없습니다. 이미지를 선택하세요.');
  assert.equal(generateBlockedReason({...base,label:' '},'image'),'후보 클래스가 비어 있습니다. 클래스 이름을 입력하세요.');
  assert.equal(generateBlockedReason(base,'batch',0),'선택한 이미지가 없습니다. 일괄 실행할 이미지를 고르세요.');
  assert.equal(generateBlockedReason({...base,setup:null},'image'),'모델 준비 상태를 읽는 중입니다. 잠시 기다리세요.');
  assert.equal(generateBlockedReason({...base,currentScope:false},'image'),'프로젝트가 바뀌어 준비 상태를 다시 읽는 중입니다. 잠시 뒤 다시 시도하세요.');
});

test('the e2e-pinned names stay: the region, the generate button, the exclusion summary and its input',async()=>{
  const ui=mount({setup:setupWith(readiness('not_configured'))});await ui.open();
  assert.ok(ui.byLabel('SAM2 기반 라벨링'));
  assert.ok(ui.generateButton());
  assert.ok(ui.nodes().some(node=>node.type==='summary'&&/^현재 이미지 제외 영역 · /.test(ui.text(node))));
  assert.ok(ui.byLabel('제외할 원본 영역'));
  assert.ok(ui.button('positive 예시 추가'));
  assert.ok(ui.nodes().some(node=>node.type==='summary'&&/^이미지 영역 예시 · /.test(ui.text(node))));
});

test('before setup is read the card says 확인 중, and an empty weight file is 파일 손상 with no 준비됨 beside its 0B row',async()=>{
  // SHA256 of zero bytes: what the backend reports for an interrupted copy that left model.safetensors empty.
  const emptySha='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';
  const ready=readiness('ready',{expected_files:files([true,true,true],[812,520,0]),missing_files:[],model_type:'sam2',weights_check:'empty',model_sha256:emptySha});
  const ui=mount({setup:setupWith(ready)});
  ui.render();
  assert.equal(ui.status(),'SAM2 모델 준비 · 확인 중','the label does not contradict the cause while GET /setup is pending');
  assert.equal(ui.details().props.open,true);
  assert.match(ui.cardText(),/모델 준비 상태를 아직 읽지 못했습니다\. 파일 확인을 누르세요\./);
  assert.equal(ui.reason(),'모델 준비 상태를 읽는 중입니다. 잠시 기다리세요.');
  await ui.settle();ui.render();
  assert.equal(ui.status(),'SAM2 모델 준비 · 파일 손상','the backend gate passes an empty file; the card does not call it ready');
  assert.match(ui.cardText(),/model\.safetensors\(측정 크기 0B\)가 빈 파일입니다\. 공식 페이지에서 model\.safetensors를 다시 받아 이 폴더에 두세요\./);
  assert.doesNotMatch(ui.cardText(),/준비됨|찾았습니다/);
  const items=ui.nodes().filter(node=>node.type==='li'&&/\.json|\.safetensors/.test(ui.text(node))).map(node=>ui.text(node));
  assert.deepEqual(items,['✓ config.json · 812B','✓ preprocessor_config.json · 520B','✓ model.safetensors · 0B · 빈 파일 · 다시 받으세요'],'the checklist does not vouch for an empty weight file');
  assert.equal(ui.generateButton().props.disabled,true);
  assert.match(ui.reason(),/^SAM2 모델이 준비되지 않았습니다\(파일 손상\)\. model\.safetensors\(측정 크기 0B\)가 빈 파일입니다\./);
  // An older backend reports no weights_check: its empty file still opens the card and flags the row, under the backend's 준비됨.
  const older=mount({setup:setupWith(readiness('ready',{expected_files:files([true,true,true],[812,520,0]),missing_files:[],model_type:'sam2'}))});await older.open();
  assert.equal(older.status(),'SAM2 모델 준비 · 준비됨');
  assert.equal(older.details().props.open,true,'an empty file keeps the card open');
});

test('a git-lfs pointer or a truncated weight file is 파일 손상 with its measured size, a Korean reason and one action, and both generate buttons stop',async()=>{
  const cases=[
    ['not_safetensors',134,/^model\.safetensors\(측정 크기 134B\)가 safetensors 형식이 아닙니다\(git-lfs 포인터 등\)\. 공식 페이지에서 model\.safetensors를 다시 받아 이 폴더에 두세요\.$/,'✓ model.safetensors · 134B'],
    ['truncated',40*1048576,/^model\.safetensors\(측정 크기 40\.0MB\)가 헤더에 적힌 크기와 맞지 않습니다\(받다 끊긴 파일 등\)\. 공식 페이지에서/,'✓ model.safetensors · 40.0MB'],
  ];
  for(const [check,bytes,cause,row] of cases){
    const damaged=readiness('ready',{expected_files:files([true,true,true],[812,520,bytes]),missing_files:[],model_type:'sam2',weights_check:check,model_sha256:'cd'.repeat(32)});
    const ui=mount({setup:setupWith(damaged)});await ui.open();
    assert.equal(ui.status(),'SAM2 모델 준비 · 파일 손상',check);
    assert.equal(ui.details().props.open,true,check);
    assert.match(ui.exports.sam2ReadinessView(damaged).cause,cause,check);
    assert.doesNotMatch(ui.cardText(),/준비됨|찾았습니다|읽을 수 있습니다/,check);
    const weights=ui.nodes().find(node=>node.type==='li'&&/model\.safetensors/.test(ui.text(node)));
    assert.equal(ui.text(weights),row,check);
    assert.match(weights.props.className,/text-amber-200/,`${check}: the row is not green`);
    assert.deepEqual(ui.primary(),['공식 모델 페이지 열기'],check);
    assert.equal(ui.generateButton().props.disabled,true,`${check}: no click that only the loader would refuse`);
    assert.equal(ui.reason(),`SAM2 모델이 준비되지 않았습니다(파일 손상). ${ui.exports.sam2ReadinessView(damaged).cause}`,check);
    assert.equal(ui.button('비동기 일괄 후보 시작').props.disabled,true,`${check}: the batch button stops too`);
  }
});
