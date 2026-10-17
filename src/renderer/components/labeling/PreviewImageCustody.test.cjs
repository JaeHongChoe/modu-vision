const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

// Execute the real child components and real dataWorkbench helper. React hooks,
// stores and the external transport are controlled; no application/backend is
// started. The asserted resource URLs are emitted by the production JSX itself.
const SOURCE = process.env.PREVIEW_IMAGE_CUSTODY_SOURCE_ROOT || path.resolve(__dirname, '../../../..');
const BORROW = process.env.PREVIEW_IMAGE_CUSTODY_BORROW_ROOT || SOURCE;
const componentDir = path.join(SOURCE, 'src/renderer/components/labeling');
const image = (id, file) => ({image_id:id, file_name:path.posix.basename(file), file_path:file,
  thumbnail_url:'/api/dataset/thumbnail/'+path.posix.basename(file)+'?file_path='+file});

function fixture({busy=false, source='/data/a', file='/data/a/part.dcm', external=null}={}) {
  let cursor=0, dirty=false, mounted=true, tree=[];
  const slots=[], effects=[], history=[], selected=[];
  const current=image('a',file);
  const project={projectDir:'/projects/a', project:{id:'a',task:'segmentation',source_dataset_dir:source,active_labelset_id:'default'},
    task:'segmentation',isProjectBusy:busy,backendPort:8000};
  const annotation={currentImage:current,images:[current],currentImageIndex:0,externalSelectionPath:external,
    metadata:{width:32,height:16,revision:'r1',content_hash:'a'.repeat(64)},isDirty:false,isSaving:false,
    annotationLoadStatus:'ready',annotations:[],reviewerName:'controlled source operator',
    selectImageByIndex:async index=>{selected.push(index);},nextImage:async()=>{},prevImage:async()=>{},saveAnnotations:async()=>true};
  const compute={selectedProfileId:'local',transportRevision:0};
  const dataset={page:1,pageSize:50,totalImagesCount:1,images:annotation.images,loadImages:async()=>{}};
  const hook=value=>Object.assign(()=>value,{getState:()=>value});
  const same=(a,b)=>a?.length===b?.length && a.every((v,i)=>Object.is(v,b[i]));
  const react={
    useRef(value){const i=cursor++;return slots[i]||(slots[i]={current:value});},
    useState(value){const i=cursor++;if(!(i in slots))slots[i]=typeof value==='function'?value():value;
      return [slots[i],next=>{assert.ok(mounted,'A settled old transport callback must not write after unmount');
        const v=typeof next==='function'?next(slots[i]):next;if(!Object.is(v,slots[i])){slots[i]=v;dirty=true;}}];},
    useEffect(fn,deps){const i=cursor++;if(!same(slots[i]?.deps,deps)){const old=slots[i];slots[i]={deps};
      effects.push({old,install:()=>{slots[i].cleanup=fn();}});}},
  };
  const jsx=(type,props)=>({type,props});
  const api={resolveApiUrl:route=>'http://127.0.0.1:8000'+route,getApiPersistenceIdentity:()=>`local:${project.project.id}`,
    request(route){assert.match(route,/^\/api\/data-workbench\/derived\?image_path=/);
      let resolve;const promise=new Promise(done=>{resolve=done;});history.push({route,project:project.project.id,resolve});return promise;}};
  const modules=new Map();
  const mocks={
    react:{__esModule:true,default:react,...react},'react/jsx-runtime':{jsx,jsxs:jsx,Fragment:'fragment'},
    'lucide-react':{ChevronLeft:()=>null,ChevronRight:()=>null},
    '../../stores/useAnnotationStore':{useAnnotationStore:hook(annotation)},
    '../../stores/useProjectStore':{useProjectStore:hook(project)},
    '../../stores/useDatasetStore':{useDatasetStore:hook(dataset)},
    '../../stores/useComputeStore':{useComputeStore:hook(compute)},
    '../../services/hostAdapter':{host:{openPath:async()=>{}},hostErrorMessage:()=>''},
    '../../services/api':api,'./api':api,
  };
  function load(file){
    if(modules.has(file))return modules.get(file).exports;
    const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));modules.set(file,m);
    const ordinary=m.require.bind(m);
    m.require=name=>{
      if(Object.hasOwn(mocks,name))return mocks[name];
      if(name.startsWith('.')){
        const stem=path.resolve(path.dirname(file),name);
        const stems=[stem,path.join(BORROW,path.relative(SOURCE,stem))];
        for(const candidate of stems)for(const ext of ['.ts','.tsx'])if(fs.existsSync(candidate+ext))return load(candidate+ext);
      }
      return ordinary(name);
    };
    m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,
      target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);return m.exports;
  }
  const Derived=load(path.join(componentDir,'DerivedImagePanel.tsx')).DerivedImagePanel;
  const Filmstrip=load(path.join(componentDir,'ImageFilmstrip.tsx')).ImageFilmstrip;
  const nodes=value=>Array.isArray(value)?value.flatMap(nodes):value&&typeof value==='object'?
    [value,...nodes(value.props?.children)]:[];
  function render(){assert.ok(mounted);dirty=true;for(let i=0;i<20&&dirty;i++){
    cursor=0;dirty=false;tree=[Derived(),Filmstrip({compact:false})];
    const queued=effects.splice(0);queued.forEach(e=>e.old?.cleanup?.());queued.forEach(e=>e.install());
  }assert.equal(dirty,false,'Production child renders must settle');return tree;}
  const urls=()=>nodes(tree).filter(n=>n.type==='image'||n.type==='img').map(n=>n.props.href||n.props.src).filter(Boolean);
  async function close(){slots.forEach(s=>s?.cleanup?.());mounted=false;for(const row of history)row.resolve({versions:[]});
    await Promise.resolve();await Promise.resolve();}
  render();return {annotation,project,dataset,history,selected,render,urls,nodes:()=>nodes(tree),close};
}

test('actual children emit current source raw preview, thumbnail and history',async()=>{
  const f=fixture();try{assert.deepEqual(f.urls(),[
    'http://127.0.0.1:8000/api/dataset/raw/part.dcm?file_path=%2Fdata%2Fa%2Fpart.dcm',
    'http://127.0.0.1:8000/api/dataset/thumbnail/part.dcm?file_path=/data/a/part.dcm']);
    assert.deepEqual(f.history.map(r=>r.route),['/api/data-workbench/derived?image_path=%2Fdata%2Fa%2Fpart.dcm']);
  }finally{await f.close();}
});

test('busy project suppresses actual SVG, thumbnail and history before a read is constructed',async()=>{
  const f=fixture({busy:true});try{assert.deepEqual(f.urls(),[]);assert.deepEqual(f.history,[]);}finally{await f.close();}
});

test('project switch with old A selection cannot schedule sibling resources while B is busy',async()=>{
  const f=fixture();try{const before=f.history.length;f.project.isProjectBusy=true;f.project.projectDir='/projects/b';
    f.project.project={id:'b',task:'segmentation',source_dataset_dir:'/data/b',active_labelset_id:'default'};f.render();
    assert.deepEqual(f.urls(),[]);assert.equal(f.history.length,before);
  }finally{await f.close();}
});

test('old A selection remains ineligible after B busy settles',async()=>{
  const f=fixture();try{const before=f.history.length;f.project.projectDir='/projects/b';
    f.project.project={id:'b',task:'segmentation',source_dataset_dir:'/data/b',active_labelset_id:'default'};
    f.project.isProjectBusy=false;f.render();assert.deepEqual(f.urls(),[]);assert.equal(f.history.length,before);
  }finally{await f.close();}
});

test('filmstrip keeps original row indices when earlier rows are ineligible',async()=>{
  const f=fixture({source:'/data/b',file:'/data/b/b.dcm'});try{
    f.annotation.images=[image('old','/data/a/old.dcm'),image('b','/data/b/b.dcm')];f.annotation.currentImageIndex=1;
    f.dataset.totalImagesCount=2;f.render();assert.equal(f.urls().some(u=>u.includes('old.dcm')),false);
    const row=f.nodes().find(n=>n.type==='div'&&typeof n.props.onClick==='function'&&f.nodes().includes(n)
      &&JSON.stringify(n.props.children).includes('b.dcm'));
    assert.ok(row);await row.props.onClick();assert.deepEqual(f.selected,[1]);
  }finally{await f.close();}
});

test('explicit external evidence path remains eligible outside the gallery',async()=>{
  const f=fixture({source:'/data/b',external:'/data/a/part.dcm'});try{assert.equal(f.urls().length,2);
    assert.ok(f.urls().every(u=>u.includes('part.dcm')));assert.equal(f.history.length,1);
  }finally{await f.close();}
});

test('external evidence cannot bypass the busy transition',async()=>{
  const f=fixture({busy:true,source:'/data/b',external:'/data/a/part.dcm'});try{assert.deepEqual(f.urls(),[]);
    assert.deepEqual(f.history,[]);
  }finally{await f.close();}
});

test('a sibling prefix is not a file below the selected source directory',async()=>{
  const f=fixture({source:'/data/a',file:'/data/a-other/part.dcm'});try{assert.deepEqual(f.urls(),[]);
    assert.deepEqual(f.history,[]);
  }finally{await f.close();}
});

test('settling the same source starts current history after its busy read was suppressed',async()=>{
  const f=fixture({busy:true});try{assert.deepEqual(f.urls(),[]);assert.equal(f.history.length,0);
    f.project.isProjectBusy=false;f.render();assert.equal(f.urls().length,2);assert.equal(f.history.length,1);
  }finally{await f.close();}
});

test('a new explicit evidence selection starts history only after it becomes eligible',async()=>{
  const f=fixture({source:'/data/b'});try{assert.deepEqual(f.urls(),[]);assert.equal(f.history.length,0);
    f.annotation.externalSelectionPath='/data/a/part.dcm';f.render();assert.equal(f.urls().length,2);
    assert.equal(f.history.length,1);
  }finally{await f.close();}
});

test('replacement of the exact selected image object starts its own history receive',async()=>{
  const f=fixture();try{assert.equal(f.history.length,1);f.annotation.currentImage={...f.annotation.currentImage};
    f.render();assert.equal(f.history.length,2);
  }finally{await f.close();}
});
