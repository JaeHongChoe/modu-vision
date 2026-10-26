const assert=require('node:assert/strict'),test=require('node:test'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file){const name=path.resolve(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);const req=m.require.bind(m);m.require=ref=>ref==='./jargonEnglish'?load('jargonEnglish.ts'):req(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
test('every displayed glossary term has complete English help without undefined cards',()=>{const m=load('jargonDictionary.ts');for(const key of Object.keys(m.JARGON_DICTIONARY)){const entry=m.getJargonEntry(key,'en');assert.ok(entry,key);for(const field of ['termKo','definitionKo','shopFloorMeaningKo','recommendedValueKo']){assert.ok(entry[field],key+':'+field);assert.doesNotMatch(entry[field],/[가-힣]/,key+':'+field);}}assert.equal(m.getJargonEntry('unknown','en'),undefined);});

test('AP variants describe averaging precision and their own IoU matching rules in both languages',()=>{
 const m=load('jargonDictionary.ts');for(const language of ['ko','en']){
  const single=m.getJargonEntry('map_50',language),range=m.getJargonEntry('map_50_95',language);assert.ok(range);
  assert.match(single.definitionKo,/AP|average precision/i);assert.match(range.definitionKo,/0\.5.*0\.05.*0\.95/);
  assert.notEqual(single.definitionKo,range.definitionKo);assert.doesNotMatch(single.termKo+single.definitionKo,/평균.*정확도/);
 }
});
test('named metric and Focal guidance requires task evidence rather than universal operating targets',()=>{
 const m=load('jargonDictionary.ts');for(const key of ['focal_loss','auroc','map_50','map_50_95','iou','dice']){
  const entry=m.getJargonEntry(key,'ko');assert.ok(entry,key);
  assert.doesNotMatch(entry.shopFloorMeaningKo+entry.recommendedValueKo,/글로벌 표준|실투입 기준|최우수 A등급|미검.*방지합니다|0\.(?:95|98|70|85|80|75).*이상/);
  assert.match(entry.recommendedValueKo,/검증|독립|평가|확인/,key);
 }
});
