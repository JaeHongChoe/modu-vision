'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const crypto=require('node:crypto');

function fixture(){
  const root=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'modu-license-bundle-')));
  const entry=root+'/node_modules/example';fs.mkdirSync(entry,{recursive:true});
  fs.writeFileSync(entry+'/package.json',JSON.stringify({name:'example',version:'1.2'}));
  fs.writeFileSync(entry+'/LICENSE','original copyright and license\n');
  fs.writeFileSync(root+'/package-lock.json',JSON.stringify({lockfileVersion:3,packages:{'':{},'node_modules/example':{version:'1.2',license:'MIT'}}}));
  return root;
}

test('actual package license bytes ship and tampering fails readback',()=>{
  const {collectDesktopLicenses,verifyBundle}=require('../../build/package-license-texts.cjs');
  const root=fixture();const target=root+'/bundle';const before=fs.readFileSync(root+'/node_modules/example/LICENSE');
  const receipt=collectDesktopLicenses(root,target);
  assert.equal(receipt.status,'collected');assert.deepEqual(receipt.missing,[]);
  const row=receipt.files[0];assert.deepEqual(fs.readFileSync(target+'/'+row.path),before);
  assert.equal(row.sha256,crypto.createHash('sha256').update(before).digest('hex'));
  assert.ok(!JSON.stringify(receipt).includes(root));verifyBundle(target);
  fs.writeFileSync(target+'/'+row.path,'changed');assert.throws(()=>verifyBundle(target),/checksum/);
});

for(const kind of ['missing','wrong-version','linked-leaf','linked-parent','existing-output']){
  test('desktop collection refuses '+kind,()=>{
    const {collectDesktopLicenses}=require('../../build/package-license-texts.cjs');
    const root=fixture();const target=root+'/bundle';const entry=root+'/node_modules/example';
    if(kind==='missing')fs.unlinkSync(entry+'/LICENSE');
    if(kind==='wrong-version')fs.writeFileSync(entry+'/package.json',JSON.stringify({name:'example',version:'2.0'}));
    if(kind==='linked-leaf'){fs.renameSync(entry+'/LICENSE',root+'/outside');fs.symlinkSync(root+'/outside',entry+'/LICENSE');}
    if(kind==='linked-parent'){fs.renameSync(entry,root+'/outside');fs.symlinkSync(root+'/outside',entry,'dir');}
    if(kind==='existing-output')fs.mkdirSync(target);
    assert.throws(()=>collectDesktopLicenses(root,target),/missing|version|linked|exists/);
    assert.equal(fs.existsSync(target+'/manifest.json'),false);
  });
}

test('Electron runtime license and Chromium notices survive desktop packaging',()=>{
  const {collectDesktopLicenses}=require('../../build/package-license-texts.cjs');
  const root=fixture();const electron=root+'/node_modules/electron';fs.mkdirSync(electron+'/dist',{recursive:true});
  fs.writeFileSync(electron+'/package.json',JSON.stringify({name:'electron',version:'44.5.1'}));
  fs.writeFileSync(electron+'/LICENSE','electron package license');
  fs.writeFileSync(electron+'/dist/LICENSE','runtime license');
  fs.writeFileSync(electron+'/dist/LICENSES.chromium.html','<html>chromium licenses</html>');
  const lock=JSON.parse(fs.readFileSync(root+'/package-lock.json'));lock.packages['node_modules/electron']={version:'44.5.1',dev:true,license:'MIT'};fs.writeFileSync(root+'/package-lock.json',JSON.stringify(lock));
  const receipt=collectDesktopLicenses(root,root+'/bundle');
  assert.ok(receipt.files.some(row=>row.source==='node_modules/electron/dist/LICENSES.chromium.html'));
  assert.ok(receipt.files.some(row=>row.source==='node_modules/electron/dist/LICENSE'));
});

test('a license readback rejects unlisted files and duplicate paths',()=>{
  const {collectDesktopLicenses,verifyBundle}=require('../../build/package-license-texts.cjs');
  const root=fixture();const target=root+'/bundle';const receipt=collectDesktopLicenses(root,target);
  fs.writeFileSync(target+'/unlisted.txt','extra');assert.throws(()=>verifyBundle(target),/unlisted/);
  fs.unlinkSync(target+'/unlisted.txt');receipt.files.push(receipt.files[0]);
  fs.writeFileSync(target+'/manifest.json',JSON.stringify(receipt));assert.throws(()=>verifyBundle(target),/path/);
});

function supplementFixture(){
  const root=fixture(),name='react-remove-scroll-bar',entry=root+'/node_modules/'+name;
  fs.mkdirSync(entry);fs.writeFileSync(entry+'/package.json',JSON.stringify({name,version:'2.3.8',license:'MIT'}));
  const lock=JSON.parse(fs.readFileSync(root+'/package-lock.json'));
  lock.packages['node_modules/'+name]={version:'2.3.8',license:'MIT',integrity:'sha512-9r+yi9+mgU33AKcj6IbT9oRCO78WriSj6t/cF8DWBZJ9aOGPOTEDvdUDz1FwKim7QXWwmHqtdHnRJfhAxEG46Q=='};
  fs.writeFileSync(root+'/package-lock.json',JSON.stringify(lock));
  const source=path.resolve(__dirname,'../../public/third-party/radix-popover'),supplier=root+'/public/third-party/radix-popover';
  fs.mkdirSync(supplier,{recursive:true});for(const file of ['LICENSES.txt','provenance.json'])fs.copyFileSync(source+'/'+file,supplier+'/'+file);
  return {root,entry,supplier};
}

test('the exact locked missing-license package ships verified existing upstream supplement bytes',()=>{
  const {collectDesktopLicenses,verifyBundle}=require('../../build/package-license-texts.cjs');
  const {root}=supplementFixture(),receipt=collectDesktopLicenses(root,root+'/bundle');
  const row=receipt.files.find(row=>row.component==='react-remove-scroll-bar@2.3.8');
  assert.ok(row);assert.equal(row.size,1093);assert.equal(row.sha256,'a79aae0c0f21990d9d963bb3c5a79cdcea9a46f8523ba55c58d7fe776b6ebc84');
  assert.match(row.source,/^public\/third-party\/radix-popover\/LICENSES\.txt/);
  assert.equal(row.upstream_source,'https://api.github.com/repos/theKashey/react-remove-scroll-bar/git/blobs/7c08c3990396ecefd90f99ff5d9a34f26f5b5616');
  assert.equal(fs.existsSync(root+'/node_modules/react-remove-scroll-bar/LICENSE'),false,'never injects files into installed dependencies');
  assert.equal(verifyBundle(root+'/bundle').status,'collected');assert.equal(receipt.public_distribution_approved,false);
});

for(const kind of ['wrong-integrity','wrong-version','changed-body','wrong-source','wrong-size','duplicate-provenance','linked-supplement'])test('upstream supplement refuses '+kind,()=>{
  const {collectDesktopLicenses}=require('../../build/package-license-texts.cjs');const {root,entry,supplier}=supplementFixture();
  if(kind==='wrong-integrity'||kind==='wrong-version'){
    const lock=JSON.parse(fs.readFileSync(root+'/package-lock.json'));
    if(kind==='wrong-integrity')lock.packages['node_modules/react-remove-scroll-bar'].integrity='sha512-other';
    else{lock.packages['node_modules/react-remove-scroll-bar'].version='2.3.9';fs.writeFileSync(entry+'/package.json',JSON.stringify({name:'react-remove-scroll-bar',version:'2.3.9',license:'MIT'}));}
    fs.writeFileSync(root+'/package-lock.json',JSON.stringify(lock));
  }
  if(kind==='changed-body')fs.writeFileSync(supplier+'/LICENSES.txt',fs.readFileSync(supplier+'/LICENSES.txt','utf8').replace('Copyright (c) 2025 Anton','Copyright (c) 2024 Anton'));
  if(kind==='wrong-size')fs.writeFileSync(supplier+'/LICENSES.txt',fs.readFileSync(supplier+'/LICENSES.txt','utf8').replace('===== react-remove-scroll-bar@2.3.8 / LICENSE =====\nMIT License','===== react-remove-scroll-bar@2.3.8 / LICENSE =====\nMIT LicenseX'));
  if(kind==='wrong-source'||kind==='duplicate-provenance'){
    const rows=JSON.parse(fs.readFileSync(supplier+'/provenance.json')),row=rows.find(row=>row.package==='react-remove-scroll-bar');
    if(kind==='wrong-source')row.source='https://example.com/unpinned';else rows.push(row);
    fs.writeFileSync(supplier+'/provenance.json',JSON.stringify(rows));
  }
  if(kind==='linked-supplement'){fs.renameSync(supplier+'/LICENSES.txt',root+'/outside-license');fs.symlinkSync(root+'/outside-license',supplier+'/LICENSES.txt');}
  assert.throws(()=>collectDesktopLicenses(root,root+'/bundle'),/supplement|license|linked/);
  assert.equal(fs.existsSync(root+'/bundle/manifest.json'),false);
});
