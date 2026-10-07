'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const crypto=require('node:crypto');

function fixture(){
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'modu-license-bundle-'));
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
