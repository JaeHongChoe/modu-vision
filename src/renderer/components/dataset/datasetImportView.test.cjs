const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file){const name=path.join(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);m.require=ref=>ref.startsWith('.')?load(ref+'.ts'):require(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const view=()=>load('datasetImportView.ts');
const job=(state,extra={})=>({job_id:'j',state,revision:3,attempts:1,cancel_requested:false,progress:null,result:null,...extra});
const receipt=(extra={})=>({revision_id:'r1',state:'prepared',manifest_sha256:'m',image_count:6,valid_count:6,error_count:0,invalid_policy:'exclude',skipped_links:0,unreadable_folders:0,reused_entries:0,verified_all:true,...extra});
test('progress shows no percentage until the server knows the total',()=>{const v=view();
 assert.deepEqual(v.importProgress(job('running',{progress:{phase:'listing',processed:0,total:null,total_known:false}})),{text:'이미지 목록 확인 중',percent:null});
 assert.deepEqual(v.importProgress(job('running',{progress:{phase:'reading',processed:3,total:12,total_known:true}})),{text:'3 / 12장 확인',percent:25});
 assert.equal(v.importProgress(job('completed')).percent,null,'a finished job without live progress (after a restart) shows its state');});
test('only the completed job\'s own prepared revision can be adopted',()=>{const v=view();
 assert.match(v.acceptBlocker(job('running'),[]),/완료된 가져오기/);
 assert.match(v.acceptBlocker(job('completed',{result:{revision:receipt({state:'rejected',error_count:2})}}),[]),/거부 정책/);
 assert.match(v.acceptBlocker(job('completed',{result:{revision:receipt()}}),[{revision_id:'r1',active:true}]),/이미 활성/);
 assert.equal(v.acceptBlocker(job('completed',{result:{revision:receipt()}}),[{revision_id:'r1',active:false}]),null);
 assert.match(v.acceptBlocker(job('completed',{result:{revision:receipt({state:'rejected',error_count:0,unreadable_folders:2})}}),[]),/읽지 못한 폴더 2개/);
 assert.match(v.acceptBlocker(job('completed',{result:{revision:receipt({image_count:0,valid_count:0})}}),[]),/하나도 없는/);});
test('a failed job names its reason and a pending stop is visible',()=>{const v=view();
 assert.equal(v.importStatusText(job('failed',{result:{error:{message:'Source folder does not exist'}}})),'실패: Source folder does not exist');
 assert.match(v.importStatusText(job('running',{cancel_requested:true})),/중지 요청됨/);
 assert.match(v.importStatusText(job('aborted',{cancel_requested:true})),/버전 없음/);});
test('a sampled or partial quick inspection never reads as a clean dataset',()=>{const v=view();
 assert.equal(v.quickValidationNotice({requested:true,checked_images:12,complete:true,scope:'all images: every image file under the folder was decoded'}),null);
 assert.match(v.quickValidationNotice({requested:true,checked_images:201,complete:false,scope:'sampled: first 201 images in folder order'}),/처음 201장만/);
 assert.match(v.quickValidationNotice({requested:true,checked_images:3,complete:false,scope:'partial: 3 image files under the folder decoded; the inventory is listed by annotation files, which the dataset index validates'}),/일부/);
 assert.equal(v.quickValidationNotice(undefined),null,'an older backend without the field shows nothing new');});
test('live progress, a late stop and the registered source are stated as they are',()=>{const v=view();
 assert.match(v.importProgress(job('running')).text,/실행하는 앱에서만/);
 assert.equal(v.importProgress(job('running',{progress:{phase:'listing',processed:0,total:null,total_known:false}})).text,'이미지 목록 확인 중');
 assert.match(v.importStatusText(job('completed',{cancel_requested:true,result:{revision:receipt()}})),/중지 요청 전에 검증이 끝났습니다/);
 assert.equal(v.sourceMismatchNotice('/data/a/','/data/a'),null);
 assert.match(v.sourceMismatchNotice('/data/b','/data/a'),/등록된 원본을 읽습니다/);
 assert.match(v.sourceMismatchNotice('/data/b',null),/등록된 원본 폴더가 없습니다/);});
test('the header says active, a waiting record is stated, and one key serves one intended import',()=>{const v=view();
 const done=job('completed',{result:{revision:receipt()}});
 assert.equal(v.importHeadline(done,[{revision_id:'r1',active:true}]),'검증 완료 · 활성 버전');
 assert.equal(v.importHeadline(done,[{revision_id:'r1',active:false}]),'검증 완료 · 채택 전');
 assert.match(v.importProgress(job('running',{progress:{phase:'recording'}})).text,/기록 대기/);
 let made=0;const make=()=>`k${++made}`;
 assert.equal(v.importKeyFor('p|src|cls',make),'k1');assert.equal(v.importKeyFor('p|src|cls',make),'k1','a retry reuses the key');
 assert.equal(v.importKeyFor('p|other|cls',make),'k2','another source is another import');
 v.clearImportKey('p|src|cls');assert.equal(v.importKeyFor('p|src|cls',make),'k3','after a successful start the next import gets a new key');
 assert.equal(v.revisionJobLabel({publication_key:'0123456789abcdef'}),'작업 01234567');assert.equal(v.revisionJobLabel({publication_key:null}),'작업 기록 없음');});
test('annotations and duplicates are stated from the receipt, and older receipts say they never recorded them',()=>{const v=view();
 assert.match(v.annotationSummary(receipt()),/기록하기 전에/,'a schema-2 receipt has no annotation counts');
 assert.equal(v.annotationSummary(receipt({annotated:4,annotation_errors:0})),'주석 파일(LabelMe·COCO·YOLO)이 연결된 이미지 4장');
 assert.match(v.annotationSummary(receipt({annotated:4,annotation_errors:2})),/주석 오류 2장\(손상 수에 포함/);
 assert.equal(v.duplicateSummary(receipt()),null);
 assert.equal(v.duplicateSummary(receipt({duplicate_groups:0,duplicate_images:0})),'같은 내용의 이미지가 없습니다.');
 const both=v.duplicateSummary(receipt({duplicate_groups:2,duplicate_images:5,conflicting_duplicates:1,cross_split_duplicates:1}));
 assert.match(both,/같은 내용 그룹 2개\(이미지 5장\)/);assert.match(both,/라벨이 다른 그룹 1개/);assert.match(both,/섞인 그룹 1개/);assert.match(both,/지우지 않고/);
 assert.match(v.acceptBlocker(job('completed',{result:{revision:receipt({valid_count:0,error_count:6})}}),[]),/정상 이미지가 하나도 없는/);
 assert.match(v.acceptBlocker(job('completed',{result:{revision:receipt({state:'rejected',error_count:2})}}),[]),/손상·주석 오류 2장/);});
test('a receipt recovered from before schema 3 and a folder-labelled task are worded as they are',()=>{const v=view();
 assert.match(v.annotationSummary(receipt({annotated:null,duplicate_groups:null})),/기록하기 전에/,'null means never examined');
 assert.equal(v.duplicateSummary(receipt({duplicate_groups:null})),null,'never "no duplicates" for a revision that never looked');
 assert.match(v.annotationSummary(receipt({annotated:0,annotation_errors:6,annotations_bind:false})),/폴더 라벨로 학습하므로 이미지를 제외하지 않음/);
 assert.match(v.annotationSummary(receipt({annotated:0,annotation_errors:1,annotations_bind:true})),/손상 수에 포함/);});
test('a ZIP import names the archive it read and the upload step is stated honestly',()=>{const v=view();
 assert.equal(v.importSourceText(job('completed')),null,'an older backend without the field shows nothing');
 assert.equal(v.importSourceText(job('completed',{source:{root:'/p/src',artifact:null}})),'읽은 원본: 등록된 원본 폴더');
 assert.equal(v.importSourceText(job('completed',{source:{root:'/p/dataset_imports/ab',artifact:{id:'x',revision:1,sha256:'0123456789abcdef'}}})),'읽은 원본: 업로드한 ZIP · 0123456789ab');
 assert.equal(v.archiveProgressText({phase:'hashing',done:5,total:10}),'파일 확인 중(SHA-256) 50%');
 assert.match(v.archiveProgressText({phase:'uploading',done:1,total:4}),/업로드 중 25% · 끊기면 저장된 위치부터/);
 assert.equal(v.archiveProgressText({phase:'uploading',done:0,total:0}).startsWith('업로드 중 100%'),true,'an empty file never divides by zero');
 assert.equal(v.archiveProgressText({phase:'verifying',done:4,total:4}),'서버에서 파일 해시 확인 중');
 assert.equal(v.archiveProgressText(null),'');});

// The backend's answer whether the active revision still matches its source, by the index's own inventory definition.
const sourceStatus = (revision, over = {}) => ({ revision_id: revision.revision_id, matches: true, added: 0, removed: 0, changed: 0,
  gaps: 0, gaps_changed: false, images: revision.image_count, revision_images: revision.image_count, skipped_links: 0, basis: 'stat', ...over });
test('once a full validation of this source and task is the active version, the data step says so instead of warning', () => {
  const { validationStatus } = view();
  const partial = { requested: true, complete: false, checked_images: 64, scope: 'sampled_first_64' };
  const active = { revision_id: '39d55e103d26abcdef', active: true, state: 'prepared', task: 'segmentation', source_root: '/data/line3',
    image_count: 90, valid_count: 88, error_count: 2, unreadable_folders: 0, skipped_links: 0 };
  const now = sourceStatus(active);
  const done = validationStatus(partial, active, '/data/line3/', 'segmentation', now);
  assert.equal(done.tone, 'ok');
  assert.match(done.text, /전체 검증 완료 · 활성 버전 39d55e103d26 · 90장 중 유효 88장, 손상·제외 2장\./);
  assert.match(done.text, /빠른 확인\(64장\)은 미리보기 범위입니다/);
  const whole = validationStatus({ requested: true, complete: true, checked_images: 90, scope: 'all' }, active, '/data/line3', 'segmentation', now);
  assert.equal(whole.tone, 'ok');
  assert.doesNotMatch(whole.text, /미리보기/, 'a complete quick check is no preview');
  assert.doesNotMatch(validationStatus(null, { ...active, error_count: 0, valid_count: 90 }, '/data/line3', 'segmentation', now).text, /제외/);
  for (const other of [{ ...active, active: false }, { ...active, task: 'classification' }, { ...active, source_root: '/data/line4' }, { ...active, state: 'rejected' }, null]) {
    const status = validationStatus(partial, other, '/data/line3', 'segmentation', now);
    assert.equal(status.tone, 'warn', JSON.stringify(other));
    assert.match(status.text, /전체 이미지 검증은 '검증된 데이터 버전'에서 실행하세요/);
  }
  assert.equal(validationStatus(partial, active, null, 'segmentation', now).tone, 'warn', 'no registered source: no claim');
  assert.equal(validationStatus(partial, active, '/data/line3', 'segmentation', null).tone, 'warn', 'status not loaded: no claim');
  assert.equal(validationStatus(partial, active, '/data/line3', 'segmentation', { ...now, revision_id: 'another' }).tone, 'warn',
    "another revision's status: no claim");
  const windows = validationStatus(null, active, '/data/line3', 'segmentation', { ...now, basis: 'stat_without_ctime' });
  assert.equal(windows.tone, 'ok');
  assert.match(windows.text, /원본 일치는 파일 크기·수정 시각으로 확인했습니다/, 'where stat is weaker evidence, the step says how it checked');
  assert.equal(validationStatus({ requested: true, complete: true, checked_images: 88, scope: 'all' }, null, '/data/line3', 'segmentation', now), null);
});
test('a version that did not read the whole source, or is older than it, is never called complete (nqa1 review P2-3, P2-4)', () => {
  const { validationStatus } = view();
  const partial = { requested: true, complete: false, checked_images: 6, scope: 'partial: 6 image files decoded; unreadable folder' };
  const active = { revision_id: '4d155e906951ffff', active: true, state: 'prepared', task: 'classification', source_root: '/data/a',
    image_count: 6, valid_count: 6, error_count: 0, unreadable_folders: 1, skipped_links: 0 };
  const gaps = validationStatus(partial, active, '/data/a', 'classification', sourceStatus(active));
  assert.equal(gaps.tone, 'warn');
  assert.match(gaps.text, /활성 버전 4d155e906951은 원본 일부를 읽지 못했습니다\(읽지 못한 폴더 1곳\)/);
  assert.match(gaps.text, /빠른 확인 범위가 일부입니다\(6장\): partial: 6 image files decoded; unreadable folder/, "the quick check's own gap stays");
  assert.match(validationStatus(null, { ...active, unreadable_folders: 0, skipped_links: 3 }, '/data/a', 'classification', sourceStatus(active)).text, /건너뛴 링크 3개/);
  const clean = { ...active, unreadable_folders: 0 };
  const grown = validationStatus(null, clean, '/data/a', 'classification', sourceStatus(clean, { matches: false, added: 3, changed: 1, images: 9 }));
  assert.equal(grown.tone, 'warn');
  assert.match(grown.text, /활성 버전 4d155e906951\(6장\)은 지금 원본\(9장\)과 다릅니다\(추가 3장 · 바뀌었거나 확인되지 않은 1장\)/);
  assert.match(validationStatus(null, clean, '/data/a', 'classification', sourceStatus(clean, { matches: false, removed: 1, images: 5 })).text,
    /삭제 1장/, 'fewer images now');
  assert.match(validationStatus(null, clean, '/data/a', 'classification', sourceStatus(clean, { matches: false, gaps_changed: true, gaps: 1 })).text,
    /읽지 못한 폴더가 달라짐/, 'a folder unreadable since');
  assert.equal(validationStatus(null, clean, '/data/a', 'classification', sourceStatus(clean)).tone, 'ok',
    'a fresh validation of an unchanged source is complete, however the quick import counted it (nqa2 review P2-1)');
});
