const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
// E03: a measurement node's limits have a unit; mm limits need a calibration (an artifact reference or an explicit,
// fully typed manual scale), and the editor refuses what the backend refuses.
function compile(file){const name=path.join(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);
 m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const graph=compile('flowchartGraph.ts');
const REF='spatial-cal:sha256:'+'a'.repeat(64);
const paths=[{id:'width',points:[[1,1],[10,1]]}];
test('limits keep the unit they were written in, and older flows keep their meaning',()=>{
 assert.equal(graph.measurementThresholdUnit({paths}),'px');
 assert.equal(graph.measurementThresholdUnit({paths,calibration:{unit:'mm'}}),'mm','an older calibrated flow meant mm');
 assert.equal(graph.measurementThresholdUnit({paths,calibration_ref:REF}),'mm');
 assert.equal(graph.measurementThresholdUnit({paths,calibration_ref:REF,threshold_unit:'px'}),'px');});
test('a calibration reference, the limit unit and a manual scale are checked as the backend checks them',()=>{
 assert.equal(graph.measurementIssue({paths,calibration_ref:REF,threshold_unit:'mm',min_length:1}),null);
 assert.match(graph.measurementIssue({paths,calibration_ref:'line3'}),/참조가 올바르지 않습니다/);
 assert.match(graph.measurementIssue({paths,calibration_ref:REF,calibration:{unit:'mm',mm_per_pixel_x:.1,mm_per_pixel_y:.1,source_size:[20,20]}}),/함께 쓸 수 없습니다/);
 assert.match(graph.measurementIssue({paths,threshold_unit:'inch'}),/px 또는 mm/);
 assert.match(graph.measurementIssue({paths,threshold_unit:'mm'}),/mm 기준에는 교정이 필요합니다/);
 assert.match(graph.measurementIssue({paths,calibration:{unit:'mm',source_size:[20,20]},threshold_unit:'mm'}),/양수 mm\/px 교정값을 입력하세요/,'a manual scale is never a silent placeholder');});
