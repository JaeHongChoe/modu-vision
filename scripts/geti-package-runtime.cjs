'use strict';
// Package-time verification includes the complete portable dependency closure.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const sha=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const COMMIT='ddcf394d550bf1b84ca262888260043af361f5e7';
function contained(root,name,allowLeafLink=false){
  if(typeof name!=='string'||!name||name.includes('\\')||name.includes(':')||name.split('/').some(part=>!part||part==='.'||part==='..'))throw Error('Runtime inventory requires contained relative paths');
  let cursor=root;
  for(const [i,part] of name.split('/').entries()){
    cursor=path.join(cursor,part);
    if(fs.lstatSync(cursor).isSymbolicLink()&&!(allowLeafLink&&i===name.split('/').length-1))throw Error('Runtime inventory contains an unrecorded link');
  }
  return cursor;
}
function verifyRuntime(directory){
  const root=fs.realpathSync(directory),descriptorPath=path.join(root,'runtime.json');
  if(fs.lstatSync(descriptorPath).isSymbolicLink())throw Error('Runtime descriptor cannot be linked');
  const descriptor=JSON.parse(fs.readFileSync(descriptorPath,'utf8'));
  if(descriptor.version!==1||descriptor.upstream_commit!==COMMIT)throw Error('Runtime identity differs from pinned source');
  const architecture=process.arch==='x64'?'x86_64':process.arch;
  if(descriptor.platform!==process.platform||descriptor.architecture!==architecture)throw Error('Runtime target OS/architecture differs from package builder');
  const inventoryPath=contained(root,descriptor.inventory);
  if(sha(inventoryPath)!==descriptor.inventory_sha256)throw Error('Runtime inventory integrity failure');
  const inventory=JSON.parse(fs.readFileSync(inventoryPath,'utf8'));
  if(inventory.version!==1||!Array.isArray(inventory.files)||!inventory.files.length)throw Error('Runtime inventory missing');
  const names=new Set();let bytes=0;
  for(const row of inventory.files){
    const file=contained(root,row.path),stat=fs.statSync(file);
    if(names.has(row.path)||!stat.isFile()||stat.size!==row.size||sha(file)!==row.sha256)throw Error('Runtime file integrity failure: '+row.path);
    names.add(row.path);bytes+=row.size;
  }
  for(const row of inventory.links||[]){
    const file=contained(root,row.path,true),resolved=fs.realpathSync(file),relative=path.relative(root,resolved);
    if(names.has(row.path)||!fs.lstatSync(file).isSymbolicLink()||fs.readlinkSync(file)!==row.target||relative==='..'||relative.startsWith('..'+path.sep)||path.isAbsolute(relative))throw Error('Runtime link integrity failure');
    names.add(row.path);
  }
  for(const key of ['python','entrypoint'])if(!names.has(descriptor[key]))throw Error('Runtime executable inventory incomplete');
  return {status:'verified',upstream_commit:COMMIT,files:inventory.files.length,links:(inventory.links||[]).length,bytes,inventory_sha256:descriptor.inventory_sha256};
}
function completeFilteredMarkers(source,destination){
  // electron-builder's filesystem walker unconditionally omits .gitkeep.
  // Restore only inventoried EMPTY source markers, then verify the entire
  // copied runtime normally; no executable/dependency omission is excused.
  verifyRuntime(source);
  const input=fs.realpathSync(source),output=fs.realpathSync(destination);
  const descriptor=JSON.parse(fs.readFileSync(path.join(input,'runtime.json'),'utf8'));
  const inventory=JSON.parse(fs.readFileSync(contained(input,descriptor.inventory),'utf8'));
  const empty=crypto.createHash('sha256').update('').digest('hex');let restored=0;
  for(const row of inventory.files){
    if(!row.path.startsWith('source/')||path.posix.basename(row.path)!=='.gitkeep')continue;
    if(row.size!==0||row.sha256!==empty)throw Error('Only pinned empty source markers may be restored');
    let directory=output;
    for(const part of row.path.split('/').slice(0,-1)){
      directory=path.join(directory,part);
      if(fs.existsSync(directory)){
        const stat=fs.lstatSync(directory);if(stat.isSymbolicLink()||!stat.isDirectory())throw Error('Source marker destination is linked');
      }else fs.mkdirSync(directory);
    }
    const target=path.join(directory,'.gitkeep');
    if(fs.existsSync(target)||fs.lstatSync(directory).isSymbolicLink()){
      if(fs.lstatSync(target).isSymbolicLink()||sha(target)!==empty)throw Error('Source marker integrity failure');
    }else{fs.writeFileSync(target,Buffer.alloc(0),{flag:'wx',mode:0o644});restored++;}
  }
  return restored;
}
module.exports={verifyRuntime,completeFilteredMarkers};
if(require.main===module)process.stdout.write(JSON.stringify(verifyRuntime(process.argv[2]),null,2)+'\n');
