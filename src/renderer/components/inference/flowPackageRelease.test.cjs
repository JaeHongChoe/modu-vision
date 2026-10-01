const assert=require('node:assert/strict'),test=require('node:test'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file){const name=path.resolve(__dirname,file);const m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const m=load('./flowPackageRelease.ts');
const candidate=(revision_id,is_active=false)=>({revision_id,action:'approve',reviewer:'qa',reason:'r',created_at:'t',comparison_id:'c',checkpoint_sha256:'s',is_active});
const prerequisites={status:'selection_required',approval_revision_ids:{a:'ra'},approval_created:false,models:[
 {job_id:'a',task:'classification',node_ids:['n1'],checkpoint_sha256:'sa',candidates:[candidate('ra',true)],selected_revision_id:'ra',reason:null},
 {job_id:'b',task:'classification',node_ids:['n2'],checkpoint_sha256:'sb',candidates:[candidate('rb1'),candidate('rb2')],selected_revision_id:null,reason:'select_verified_revision'}]};
test('every model needs one of its own verified revisions before an approved export',()=>{
 assert.equal(m.releaseApprovalIds(prerequisites,{a:'ra'}),null);
 assert.equal(m.releaseApprovalIds(prerequisites,{a:'ra',b:'ra'}),null,'another model revision is not accepted');
 assert.deepEqual(m.releaseApprovalIds(prerequisites,{a:'ra',b:'rb2'}),{a:'ra',b:'rb2'});
 assert.equal(m.releaseApprovalIds(null,{}),null);assert.equal(m.releaseApprovalIds({...prerequisites,models:[]},{}),null);
});
test('cohort selection is bounded and defaults to the first held-out images',()=>{
 const paths=Array.from({length:70},(_,i)=>`/s/${i}.png`);
 assert.deepEqual(m.defaultCohort(paths.slice(0,1)),[]);assert.equal(m.defaultCohort(paths).length,8);assert.deepEqual(m.defaultCohort(paths.slice(0,3)),paths.slice(0,3));
 let selected=paths.slice(0,64);assert.equal(m.toggleCohort(selected,'/s/65.png').length,64);assert.equal(m.toggleCohort(selected,'/s/0.png').length,63);
});
test('parity request fields carry an explicit device and keep the one-image path separate',()=>{
 const images=[{file_path:'/s/a.png',image_id:'a'},{file_path:'/s/b.png'}];
 assert.deepEqual(m.parityFields('cohort',images,undefined,'cuda:0'),{fields:{parity_images:[{path:'/s/a.png',image_id:'a'},{path:'/s/b.png'}],parity_device:'cuda:0'}});
 assert.ok('error' in m.parityFields('cohort',images.slice(0,1),undefined,'cpu'));
 assert.deepEqual(m.parityFields('single',[],images[0],'cpu'),{fields:{verification_image_path:'/s/a.png',verification_image_id:'a'}});
 assert.ok('error' in m.parityFields('single',[],undefined,'cpu'));assert.deepEqual(m.parityFields('none',[],undefined,'cpu'),{fields:{}});
});
test('headlines never present a one-image or failed check as cohort acceptance',()=>{
 assert.equal(m.parityHeadline({status:'passed',scope:'cohort',image_count:8,device:'cpu'}).tone,'ok');
 assert.equal(m.parityHeadline({status:'passed',scope:'single_image'}).tone,'limited');
 assert.equal(m.parityHeadline({status:'mismatch',scope:'cohort'}).tone,'fail');assert.equal(m.parityHeadline({status:'failed'}).tone,'fail');
 assert.equal(m.parityHeadline({status:'not_run'}).tone,'warn');
});
