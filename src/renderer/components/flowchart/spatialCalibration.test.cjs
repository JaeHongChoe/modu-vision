const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
// E03: the measurement editor's calibration choices. A pixel limit never silently becomes a millimetre limit; a unit
// change clears the old values; the known-length form says what is missing before anything is sent.
function load(){const name=path.join(__dirname,'spatialCalibration.ts'),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);
 m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const REF='spatial-cal:sha256:'+'b'.repeat(64);
const row={ref:REF,method:'known_length_planar',camera_id:'line3-top',acquisition_config_hash:'sha256:'+'c'.repeat(64),source_size:[1280,960],
 scales_or_mapping:{mm_per_pixel_x:0.05,mm_per_pixel_y:0.07},residual:0.0032,approved_at:'2026-10-04T01:00:00+00:00',approved_by:'QA lead'};
test('a calibration is shown with its camera, method, scales, size and residual, and a size mismatch is named',()=>{const c=load();
 assert.equal(c.calibrationLabel(row),'line3-top · 기준 길이 교정 · X 0.0500 / Y 0.0700 mm/px · 1280×960 · 잔차 0.0032 mm');
 assert.match(c.calibrationLabel({...row,method:'manual_planar_scale',residual:null}),/수동 교정\(검증 안 됨\)/);
 assert.equal(c.calibrationSizeIssue(row,[1280,960]),null);
 assert.match(c.calibrationSizeIssue(row,[640,480]),/1280×960 이미지용입니다. 선택한 원본은 640×480/);});
test('choosing a calibration keeps written limits in their unit, and changing the unit clears them',()=>{const c=load();
 const px={paths:[],min_length:390,max_length:410};
 assert.deepEqual(c.withCalibrationRef(px,REF),{...px,calibration_ref:REF,threshold_unit:'px'},'pixel limits stay pixels');
 assert.deepEqual(c.withCalibrationRef({paths:[]},REF),{paths:[],calibration_ref:REF,threshold_unit:'mm'},'no limits yet: mm');
 const inline={paths:[],calibration:{unit:'mm'},min_length:2};
 assert.deepEqual(c.withCalibrationRef(inline,REF),{paths:[],calibration_ref:REF,threshold_unit:'mm',min_length:2},'mm limits stay mm');
 assert.deepEqual(c.withCalibrationRef({paths:[],calibration_ref:REF,threshold_unit:'mm',min_length:2},null),{paths:[],threshold_unit:'mm',min_length:2},'left mm limits are kept for the editor to flag');
 assert.deepEqual(c.withThresholdUnit({...px,threshold_unit:'px'},'mm'),{paths:[],threshold_unit:'mm'});
 assert.deepEqual(c.withThresholdUnit({...px,threshold_unit:'px'},'px'),{...px,threshold_unit:'px'});});
test('camera settings and the known-length form are checked before sending',()=>{const c=load();
 assert.deepEqual(c.parseAcquisitionConfig('resolution=1280x960\nlens = 16mm\nworking_distance_mm=300\n\n'),{config:{resolution:'1280x960',lens:'16mm',working_distance_mm:300},error:null});
 assert.match(c.parseAcquisitionConfig('lens').error,/이름=값/);
 assert.match(c.parseAcquisitionConfig('a=1\na=2').error,/두 번/);
 assert.match(c.parseAcquisitionConfig('').error,/카메라 설정/);
 const seg=(x2,y2,length_mm)=>({points:[[0,0],[x2,y2]],length_mm});
 const three=[seg(800,0,40),seg(0,700,49),seg(800,700,65)];
 assert.equal(c.knownLengthIssue(three,[1280,960],0.05,false),null);
 assert.match(c.knownLengthIssue(three,undefined,0.05,false),/원본 이미지를 먼저/);
 assert.match(c.knownLengthIssue(three,[1280,960],null,false),/허용 오차/);
 assert.match(c.knownLengthIssue(three.slice(0,2),[1280,960],0.05,false),/3개 이상/);
 assert.equal(c.knownLengthIssue(three.slice(0,2),[1280,960],0.05,true),null,'one scale needs two lengths');
 assert.match(c.knownLengthIssue([...three.slice(0,2),seg(5,0,1)],[1280,960],0.05,false),/10 px 이상/);
 assert.match(c.knownLengthIssue([...three.slice(0,2),seg(2000,0,1)],[1280,960],0.05,false),/원본 이미지 밖/);
 assert.match(c.knownLengthIssue([...three.slice(0,2),seg(800,700,NaN)],[1280,960],0.05,false),/실제 길이/);});
