'use strict';
// Copy vendor-provided bytes before signing. This does not decide their terms.
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const licenseName=/^(?:licen[cs]es?|copying|notices?|copyright)(?:[._-].*)?$/i;
const digest=data=>crypto.createHash('sha256').update(data).digest('hex');

function readSafe(file,base){
  const relative=path.relative(base,file);
  if(relative.startsWith('..'+path.sep)||path.isAbsolute(relative))throw new Error('license path outside package');
  let current=file;
  while(current!==base){
    if(fs.lstatSync(current).isSymbolicLink())throw new Error('linked license input');
    current=path.dirname(current);
  }
  const before=fs.lstatSync(file);
  if(!before.isFile()||before.size>32*1024*1024)throw new Error('missing or unbounded license input');
  const data=fs.readFileSync(file);const after=fs.lstatSync(file);
  if(before.ino!==after.ino||before.size!==after.size||before.mtimeMs!==after.mtimeMs)throw new Error('license changed while collecting');
  return data;
}

function licenseFiles(folder,depth=0){
  if(depth>8)throw new Error('unbounded license directory');
  const result=[];
  for(const entry of fs.readdirSync(folder,{withFileTypes:true})){
    if(entry.name==='node_modules'||entry.name.startsWith('.'))continue;
    const child=path.join(folder,entry.name);
    if(entry.isSymbolicLink()){
      if(licenseName.test(entry.name))throw new Error('linked license input');
      continue;
    }
    if(entry.isFile()&&licenseName.test(entry.name))result.push(child);
    if(entry.isDirectory()&&(/^(?:licen[cs]es?|legal|notices?|dist)$/i.test(entry.name)))result.push(...licenseFiles(child,depth+1));
  }
  return result;
}

function collectDesktopLicenses(project,destination){
  project=path.resolve(project);destination=path.resolve(destination);
  if(fs.existsSync(destination))throw new Error('license output already exists');
  const lockBytes=fs.readFileSync(path.join(project,'package-lock.json'));
  const lock=JSON.parse(lockBytes);
  const base=fs.realpathSync(path.join(project,'node_modules'));
  const inputs=[];const components=[];
  for(const [key,entry] of Object.entries(lock.packages||{})){
    if(!key)continue;
    const name=key.split('node_modules/').at(-1);
    if((entry.dev||entry.devOptional)&&!['electron','tailwindcss','vite'].includes(name))continue;
    if(!key.startsWith('node_modules/')||key.split('/').includes('..'))throw new Error('invalid locked package path');
    const folder=path.join(base,key.slice('node_modules/'.length));
    if(!fs.existsSync(folder))throw new Error('missing installed license package '+name);
    if(fs.lstatSync(folder).isSymbolicLink())throw new Error('linked package input');
    const installed=JSON.parse(readSafe(path.join(folder,'package.json'),base));
    if(installed.name!==name||installed.version!==entry.version)throw new Error('installed license package version differs from lock: '+name);
    const files=licenseFiles(folder);
    if(!files.length)throw new Error('missing full license text: '+name);
    if(name==='electron'){
      for(const leaf of ['LICENSE','LICENSES.chromium.html']){
        const runtime=path.join(folder,'dist',leaf);
        if(!fs.existsSync(runtime))throw new Error('missing Electron runtime notice '+leaf);
        files.push(runtime);
      }
    }
    components.push({name,version:entry.version,license:entry.license||'UNKNOWN'});
    for(const file of new Set(files)){
      const data=readSafe(file,base);const hash=digest(data);
      const relative=path.posix.join('texts',digest(name+'@'+entry.version).slice(0,16),hash.slice(0,16)+'-'+path.basename(file).replace(/[^A-Za-z0-9._-]/g,'_'));
      inputs.push({data,row:{component:name+'@'+entry.version,path:relative,sha256:hash,size:data.length,
                           source:path.posix.join('node_modules',path.relative(base,file).split(path.sep).join('/'))}});
    }
  }
  const parent=path.dirname(destination);fs.mkdirSync(parent,{recursive:true});
  let current=parent;
  while(current!==path.dirname(current)){if(fs.lstatSync(current).isSymbolicLink())throw new Error('linked license output');current=path.dirname(current);}
  const staging=fs.mkdtempSync(path.join(parent,'.license-texts-'));const rows=new Map();
  for(const {data,row} of inputs){const file=path.join(staging,row.path);fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,data);rows.set(row.path,row);}
  const receipt={schema_version:1,scope:'desktop_dependency_license_bytes',status:'collected',
                 public_distribution_approved:false,package_lock_sha256:digest(lockBytes),
                 components,files:[...rows.values()].sort((a,b)=>a.path.localeCompare(b.path)),missing:[]};
  fs.writeFileSync(path.join(staging,'manifest.json'),JSON.stringify(receipt,null,2)+'\n');verifyBundle(staging);
  if(digest(fs.readFileSync(path.join(project,'package-lock.json')))!==digest(lockBytes))throw new Error('package lock changed while collecting');
  if(fs.existsSync(destination))throw new Error('license output appeared while collecting');
  fs.renameSync(staging,destination);return receipt;
}

function verifyBundle(folder){
  const receipt=JSON.parse(readSafe(path.join(folder,'manifest.json'),folder));
  if(receipt.schema_version!==1||!Array.isArray(receipt.files))throw new Error('invalid license manifest');
  const listed=new Set(['manifest.json']);
  for(const row of receipt.files){
    if(path.isAbsolute(row.path)||row.path.split('/').includes('..')||listed.has(row.path))throw new Error('invalid license path');
    const data=readSafe(path.join(folder,row.path),folder);
    if(data.length!==row.size||digest(data)!==row.sha256)throw new Error('license checksum mismatch');
    listed.add(row.path);
  }
  function walk(directory){
    for(const entry of fs.readdirSync(directory,{withFileTypes:true})){
      if(entry.isSymbolicLink())throw new Error('linked license bundle input');
      const file=path.join(directory,entry.name);
      if(entry.isDirectory())walk(file);
      else if(!entry.isFile()||!listed.has(path.relative(folder,file).split(path.sep).join('/')))throw new Error('unlisted license file');
    }
  }
  walk(folder);
  return receipt;
}

module.exports={collectDesktopLicenses,verifyBundle};
