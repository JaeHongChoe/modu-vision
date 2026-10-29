const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(){const file=path.join(__dirname,'assistantImagePreparation.ts'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
function png(w,h){const b=new Uint8Array(24),d=new DataView(b.buffer);b.set([137,80,78,71,13,10,26,10],0);b.set(Buffer.from('IHDR'),12);d.setUint32(16,w);d.setUint32(20,h);return b;}
test('selected image dimensions reject empty malformed and excessive pixel counts before decode',()=>{
 const {selectedImageDimensions:dimensions}=load();assert.deepEqual(dimensions(png(4000,3000),'image/png'),[4000,3000]);
 const jpg=Uint8Array.from([255,216,255,192,0,8,8,0,32,0,48,1]);assert.deepEqual(dimensions(jpg,'image/jpeg'),[48,32]);
 const webp=new Uint8Array(30);webp.set(Buffer.from('RIFF'),0);webp.set(Buffer.from('WEBPVP8X'),8);webp[24]=63;webp[27]=31;assert.deepEqual(dimensions(webp,'image/webp'),[64,32]);
 for(const b of [new Uint8Array(),png(0,5),png(100000,100000)])assert.throws(()=>dimensions(b,'image/png'),/이미지/);
 assert.throws(()=>dimensions(jpg.subarray(0,9),'image/jpeg'),/이미지/);
});
test('normalization applies EXIF orientation, bounds size and emits fresh metadata-free bytes',async()=>{
 const {normalizeSelectedImage}=load(),oldBitmap=global.createImageBitmap,oldDocument=global.document,calls=[];let closed=0;
 global.createImageBitmap=async(file,options)=>{calls.push(options);return{width:3000,height:4000,close(){closed++;}};};
 global.document={createElement:()=>({width:0,height:0,getContext(){return{drawImage:(_bitmap,_x,_y,w,h)=>calls.push([w,h])};},toBlob(callback,type){callback(new Blob(['normalized pixels'],{type}));}})};
 try{const actual=await normalizeSelectedImage(new Blob([png(4000,3000)],{type:'image/png'}));assert.deepEqual(calls,[{imageOrientation:'from-image'},[1536,2048]]);assert.equal(await actual.text(),'normalized pixels');assert.equal(closed,1);}finally{global.createImageBitmap=oldBitmap;global.document=oldDocument;}
});
test('malformed decoder failure and oversized headers do not leave decoded resources open',async()=>{
 const {normalizeSelectedImage}=load(),oldBitmap=global.createImageBitmap;let decodes=0;global.createImageBitmap=async()=>{decodes++;throw Error('invalid image');};
 try{await assert.rejects(normalizeSelectedImage(new Blob([png(16,16)],{type:'image/png'})),/읽지/);await assert.rejects(normalizeSelectedImage(new Blob([png(10000,10000)],{type:'image/png'})),/화소/);assert.equal(decodes,1);}finally{global.createImageBitmap=oldBitmap;}
});
