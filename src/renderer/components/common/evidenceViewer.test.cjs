const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const file=path.join(__dirname,'evidenceViewer.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);
function model(){m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,file);return m.exports;}
test('viewer bounds zoom and rejects invalid ROI geometry instead of drawing another coordinate space',()=>{
 const v=model();assert.equal(v.boundedZoom(Infinity),1);assert.equal(v.boundedZoom(100),8);assert.equal(v.boundedZoom(.01),.25);
 assert.equal(v.validBox([10,20,40,50],[100,100]),true);assert.equal(v.validBox([40,20,10,50],[100,100]),false);
 assert.equal(v.validBox([0,0,120,100],[100,100]),false);assert.equal(v.validBox([0,0,NaN,10],[100,100]),false);
});
test('viewer layers accept only inline image snapshots and blend only equal coordinate spaces',()=>{
 const v=model(),a={id:'original',image:'data:image/png;base64,AA==',space:'source',size:[100,50]},b={...a,id:'overlay'};
 assert.equal(v.safeSnapshot(a.image),true);for(const value of ['https://foreign.example/image.png','file:///private/image.png','data:image/svg+xml,<svg/>'])assert.equal(v.safeSnapshot(value),false);
 assert.equal(v.canBlend(a,b),true);assert.equal(v.canBlend(a,{...b,space:'roi'}),false);assert.equal(v.canBlend(a,{...b,size:[50,25]}),false);
});

test('stored inspection rasters keep source ROI boxes separate from mask and map coordinates',()=>{
 const v=model(),png='data:image/png;base64,AA==';
 const result={annotated_image:png,inspected_image_size:[100,80],crops:[{roi_id:'fixture-roi',bbox:[10,20,50,60],crop_thumbnail:png,mask:png,anomaly_map:png,map_semantics:'pixel_score',defect_score:.75,verdict:'NG',source_transform:[[1,0,10],[0,1,20],[0,0,1]],anomaly_values:{dtype:'float32',encoding:'base64',shape:[4,4],data:'private-binary-data'},segmentation_classes:[{class_id:1,class_name:'scratch',mask:png,area_px:3}]}]};
 const evidence=v.inspectionEvidence(result,{key:'selected',title:'selected.png',runId:'run',versionId:'version',imagePath:'/source/selected.png'});
 assert.deepEqual(evidence.boxes,[{id:'fixture-roi',box:[10,20,50,60],space:'source'}]);
 assert.deepEqual(evidence.layers.map(layer=>layer.id),['overlay','roi:0:image','roi:0:mask','roi:0:map','roi:0:class:1']);
 assert.equal(evidence.runId,'run');assert.equal(evidence.versionId,'version');
 const mask=evidence.layers.find(layer=>layer.id==='roi:0:mask'),map=evidence.layers.find(layer=>layer.id==='roi:0:map');
 assert.equal(v.canBlend(mask,map),false);assert.equal(mask.facts.verdict,'NG');assert.equal(map.facts.map_semantics,'pixel_score');
 const text=JSON.stringify(evidence.facts);assert(!text.includes(png));assert(!text.includes('private-binary-data'));assert.match(text,/float32/);
});
test('stored node evidence retains only compact measurements and separate class rasters',()=>{
 const v=model(),png='data:image/png;base64,AA==';
 const layers=v.storedRasterLayers('node','ROI','roi1',png,{mask:png,anomaly_map:png,segmentation_classes:[{class_id:2,class_name:'pore',mask:png,area_px:5}],defect_score:.2,verdict:'OK'});
 assert.equal(layers.length,4);assert.equal(layers[3].label,'ROI · pore 마스크');
 assert.equal(layers[3].facts.area_px,5);assert.equal(new Set(layers.map(layer=>layer.space)).size,4);
});

test('malformed optional class evidence cannot hide readable stored rasters',()=>{
 const v=model(),png='data:image/png;base64,AA==';
 const value={segmentation_classes:[null,'unreadable',{class_id:1,class_name:'scratch',mask:png,area_px:2}]};
 const layers=v.storedRasterLayers('node','ROI','roi1',png,value);
 assert.equal(layers.length,2);assert.equal(v.compactEvidence(value).segmentation_classes.length,1);
});
