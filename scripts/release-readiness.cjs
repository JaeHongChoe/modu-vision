#!/usr/bin/env node
/* Read-only platform checks. Never installs, registers startup, or signs. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const cp = require('node:child_process');

function targetPlatform(value) {
  const map = {Darwin:'darwin', Windows:'win32', Linux:'linux', mac:'darwin', darwin:'darwin', win32:'win32', linux:'linux'};
  if (!map[value]) throw new Error('Unsupported target platform');
  return map[value];
}
function targetArch(value) {
  const map = {arm64:'arm64', aarch64:'arm64', x64:'x64', x86_64:'x64', AMD64:'x64', ia32:'ia32', x86:'ia32'};
  if (!map[value]) throw new Error('Unsupported target architecture');
  return map[value];
}
function digest(file) { return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'); }
function nativeSignature(target, platform = process.platform) {
  const run = (file, args) => cp.execFileSync(file, args, {encoding:'utf8', timeout:15000, maxBuffer:256*1024, stdio:['ignore','pipe','pipe']});
  try {
    if (platform === 'darwin') {
      run('/usr/bin/codesign', ['--verify','--deep','--strict',target]);
      let info;
      // codesign prints display information to stderr on macOS.
      const result = cp.spawnSync('/usr/bin/codesign', ['--display','--verbose=4',target], {encoding:'utf8',timeout:15000});
      if (result.status !== 0) throw new Error('Signature display failed');
      info = result.stdout + '\n' + result.stderr;
      const team = /TeamIdentifier=([^\s]+)/.exec(info)?.[1];
      if (/Signature=adhoc|flags=.*adhoc/i.test(info) || !team || team === 'not') return {status:'unsigned',reason:'Ad-hoc signature has no verified publisher'};
      return {status:'verified',publisher:team,notarization:'unverified'};
    }
    if (platform === 'win32') {
      const literal = target.replace(/'/g,"''");
      const value = JSON.parse(run('powershell.exe',['-NoProfile','-NonInteractive','-Command',`$s=Get-AuthenticodeSignature -LiteralPath '${literal}'; @{status=$s.Status.ToString();publisher=$s.SignerCertificate.Subject}|ConvertTo-Json -Compress`]));
      return {status:value.status==='Valid'?'verified':value.status==='NotSigned'?'unsigned':'invalid',publisher:value.publisher||null};
    }
    return {status:'unsigned',reason:'No Linux publisher signature verifier is configured'};
  } catch (error) {
    const detail = String(error.stderr || error.message || error);
    return {status:error.code==='ENOENT'?'unavailable':/not signed|unsigned/i.test(detail)?'unsigned':'invalid',reason:detail.slice(0,500)};
  }
}
function validateBackend(directory, platform = process.platform, arch = process.arch, {requireAcceptance=true} = {}) {
  directory = path.resolve(directory);
  const release = JSON.parse(fs.readFileSync(path.join(directory,'backend-release.json'),'utf8'));
  if (!release.executable || path.basename(release.executable) !== release.executable) throw new Error('Release executable path is invalid');
  const executable = path.join(directory,release.executable);
  if (digest(executable) !== release.executable_sha256) throw new Error('Backend executable checksum differs from the release inventory');
  if (targetPlatform(release.inventory.platform)!==targetPlatform(platform) || targetArch(release.inventory.architecture)!==targetArch(arch)) throw new Error('Frozen backend target differs from desktop package target');
  if (!Array.isArray(release.files) || !release.files.length) throw new Error('Frozen dependency file inventory is missing');
  for (const row of release.files) {
    const file = path.resolve(directory,row.path);
    if (!file.startsWith(directory + path.sep) || !fs.realpathSync(file).startsWith(fs.realpathSync(directory) + path.sep)) throw new Error('Frozen dependency path escapes backend directory');
    if (digest(file)!==row.sha256) throw new Error('Frozen dependency checksum differs: ' + row.path);
  }
  const acceptance = release.acceptance;
  if (requireAcceptance && (!acceptance || acceptance.status!=='passed' || acceptance.executable_sha256!==release.executable_sha256 || acceptance.build_identity_sha256!==release.inventory.build_identity_sha256 || targetPlatform(acceptance.platform)!==targetPlatform(platform) || targetArch(acceptance.architecture)!==targetArch(arch) || acceptance.frozen!==true || !['ok','ready'].includes(acceptance.health?.status) || !['ok','ready'].includes(acceptance.restart_health?.status))) throw new Error('Run native frozen-backend launch/restart acceptance on the actual target before packaging');
  return {executable,release,build_identity_sha256:release.inventory.build_identity_sha256,executable_sha256:release.executable_sha256,offline:release.inventory.offline};
}
function readiness(options) {
  const platform = targetPlatform(options.platform || process.platform), arch = targetArch(options.arch || process.arch);
  const report = {schema_version:1,checked_at:new Date().toISOString(),platform,architecture:arch,status:'failed',
    release_ready:false,artifact_signature_ready:false,signature:{status:'unverified'},prerequisites:[]};
  if (platform!==process.platform || arch!==process.arch) {
    report.status='requires_target';report.prerequisites.push('Run this validator on the requested operating system and architecture');return report;
  }
  try {
    const checked=validateBackend(options.backendDir,platform,arch);
    report.backend={build_identity_sha256:checked.build_identity_sha256,executable_sha256:checked.executable_sha256,offline:checked.offline,acceptance:checked.release.acceptance};
    report.signature=nativeSignature(options.app || checked.executable,platform);
    report.backend_signature=nativeSignature(checked.executable,platform);
    report.status='runtime_ready';
    if (report.signature.status!=='verified') report.prerequisites.push('Configure a publisher signing identity and verify the final delivered artifact');
    if (report.backend_signature.status!=='verified') report.prerequisites.push('Verify the frozen backend publisher signature separately from the desktop shell');
    if (report.signature.status==='verified' && report.backend_signature.status==='verified'
        && report.signature.publisher!==report.backend_signature.publisher) report.prerequisites.push('Desktop shell and frozen backend publishers differ');
    if (platform==='darwin') report.prerequisites.push('Verify notarization and Gatekeeper acceptance on the delivered installer');
    report.prerequisites.push('Physical camera/PLC and approved model acceptance require a target-specific receipt');
    report.artifact_signature_ready=report.signature.status==='verified' && report.backend_signature.status==='verified'
      && report.signature.publisher===report.backend_signature.publisher
      && (platform!=='darwin' || report.signature.notarization==='verified');
    // This probe verifies runtime/artifact observations only. A caller boolean
    // cannot approve reviewed truth, physical devices, licenses, pilot coverage
    // or a public release. Those source-bound decisions remain separate gates.
    report.release_decision='requires_reviewed_release_evidence';
  } catch (error) { report.error=String(error.message||error); }
  return report;
}
function cli(args) {
  const options={};
  for (let i=0;i<args.length;i+=2) {
    const names={'--backend-dir':'backendDir','--platform':'platform','--arch':'arch','--app':'app','--output':'output'};
    if (!names[args[i]] || !args[i+1]) throw new Error('Use --backend-dir PATH [--platform OS --arch ARCH --app PATH --output PATH]');
    options[names[args[i]]]=args[i+1];
  }
  const report=readiness(options);
  if (options.output) fs.writeFileSync(options.output,JSON.stringify(report,null,2));
  console.log(JSON.stringify(report,null,2));
  process.exitCode=['failed','requires_target'].includes(report.status)?1:0;
}
module.exports={targetPlatform,targetArch,digest,nativeSignature,validateBackend,readiness};
if (require.main===module) {
  try {cli(process.argv.slice(2));} catch(error) {console.error(String(error.message||error));process.exitCode=1;}
}
