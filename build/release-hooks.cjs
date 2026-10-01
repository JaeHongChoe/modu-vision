'use strict';
const fs = require('node:fs');
const path = require('node:path');
const checks = require('../scripts/release-readiness.cjs');

exports.beforePack = async context => {
  const platform = context.electronPlatformName;
  const arch = ({0:'ia32',1:'x64',3:'arm64'})[context.arch];
  if (platform !== process.platform || arch !== process.arch) throw new Error('Build frozen backend and run release acceptance on the actual target OS/architecture; cross-target runtime acceptance is unavailable');
  const backend=checks.validateBackend(path.join(context.packager.projectDir,'dist-backend','vision_ai_backend'),platform,arch);
  for(const row of backend.release.inventory.resources){
    const source=path.join(context.packager.projectDir,row.path);
    if(!fs.existsSync(source)||checks.digest(source)!==row.sha256)throw new Error('Backend source changed since frozen acceptance; rebuild before packaging: '+row.path);
  }
};

exports.afterPack = async context => {
  const name = context.packager.appInfo.productFilename;
  const resources = context.electronPlatformName === 'darwin'
    ? path.join(context.appOutDir,name+'.app','Contents','Resources')
    : path.join(context.appOutDir,'resources');
  const backend = checks.validateBackend(path.join(resources,'backend_bin'),context.electronPlatformName,
                                       ({0:'ia32',1:'x64',3:'arm64'})[context.arch]);
  const report={schema_version:1,status:'packaged',platform:context.electronPlatformName,
                backend:{build_identity_sha256:backend.build_identity_sha256,executable_sha256:backend.executable_sha256},
                app_asar_sha256:checks.digest(path.join(resources,'app.asar')),
                signature:{status:'unverified',reason:'Packaging precedes final signing; run release-readiness on the delivered artifact'},
                physical_acceptance:'unverified'};
  fs.writeFileSync(path.join(context.appOutDir,'release-package-diagnostics.json'),JSON.stringify(report,null,2));
};
