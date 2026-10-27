import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

// Documents, images and report folders the studio produces. Executables,
// scripts, shortcuts and application bundles are never opened from the renderer.
const OPENABLE_FILE_EXTENSIONS=new Set(['.html','.htm','.json','.csv','.txt','.md','.pdf','.png','.jpg','.jpeg',
  '.bmp','.webp','.tif','.tiff','.log','.xml','.yaml','.yml','.zip']);
const PACKAGE_DIRECTORY_EXTENSIONS=new Set(['.app','.appex','.bundle','.framework','.plugin','.prefpane','.workflow',
  '.action','.kext','.pkg','.mpkg','.xpc','.saver','.qlgenerator','.mdimporter','.osax','.component','.service','.scptd']);

export type OpenTarget={kind:'url';url:string}|{kind:'path';path:string}|{kind:'rejected';reason:string};

export function classifyOpenTarget(value:unknown):OpenTarget {
  const rejected=(reason:string):OpenTarget=>({kind:'rejected',reason});
  if(typeof value!=='string'||!value||value.length>4096)return rejected('invalid_target');
  let candidate=value;
  // A scheme, but not a Windows drive letter such as C:\
  if(/^[a-z][a-z0-9+.-]*:/i.test(value)&&!/^[a-z]:[\\/]/i.test(value)){
    let url:URL;
    try{url=new URL(value);}catch{return rejected('malformed_url');}
    if(url.protocol==='https:'||url.protocol==='http:')return {kind:'url',url:url.href};
    if(url.protocol!=='file:')return rejected('unsupported_scheme');
    if(url.host&&url.host!=='localhost')return rejected('network_path');
    try{candidate=fileURLToPath(url);}catch{return rejected('malformed_file_url');}
  }
  // UNC paths and the \\?\ / \\.\ prefixes (two leading separators) would make the
  // filesystem calls below contact a remote host, so they are refused before any access.
  if(/^[\\/]{2}/.test(candidate))return rejected('network_path');
  if(!path.isAbsolute(candidate))return rejected('relative_path');
  let real:string;
  try{real=fs.realpathSync(candidate);}catch{return rejected('missing');}
  const stat=fs.statSync(real);
  const extension=path.extname(real).toLowerCase();
  if(stat.isDirectory()){
    // Bundles execute when opened; detect them by layout, not only by name.
    if(PACKAGE_DIRECTORY_EXTENSIONS.has(extension)||fs.existsSync(path.join(real,'Contents','Info.plist')))return rejected('application_bundle');
    return {kind:'path',path:real};
  }
  if(stat.isFile()&&OPENABLE_FILE_EXTENSIONS.has(extension)){
    // A macOS alias is a bookmark file that can carry any name and resolves on open.
    const header=Buffer.alloc(16);
    const descriptor=fs.openSync(real,'r');
    try{fs.readSync(descriptor,header,0,16,0);}finally{fs.closeSync(descriptor);}
    if(header.subarray(0,4).toString('latin1')==='book'&&header.includes(Buffer.from('mark','latin1')))return rejected('alias_file');
    return {kind:'path',path:real};
  }
  return rejected('file_type_not_openable');
}
