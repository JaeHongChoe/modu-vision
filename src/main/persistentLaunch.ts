/** Read one durable spawn acknowledgement without owning the target lifetime. */
import {spawn} from 'node:child_process';

const MAX_REPLY=64*1024;
export function runPersistentController(file:string,args:string[]):Promise<{stdout:string;stderr:string}>{
  return new Promise((resolve,reject)=>{
    // Never inherit a previous application's handoff descriptors or runtime
    // injection settings. Only local OS paths/locale and fixed offline flags.
    const env:NodeJS.ProcessEnv={HF_HUB_OFFLINE:'1',TRANSFORMERS_OFFLINE:'1'};
    for(const key of ['PATH','HOME','USER','LOGNAME','LANG','LC_ALL','TMPDIR'])if(process.env[key])env[key]=process.env[key];
    const child=spawn(file,args,{shell:false,detached:true,stdio:['ignore','pipe','pipe'],env});
    let settled=false,bytes=Buffer.alloc(0),stderrBytes=0;
    const timer=setTimeout(()=>finish(Error('Launch response is unconfirmed; read the selected installation launch state')),15000);
    function finish(error?:Error,stdout?:string){
      if(settled)return;settled=true;clearTimeout(timer);
      // These are updater-owned streams only. A lost reply must not signal the
      // persistent controller, its app, or any database writer.
      child.stdout?.destroy();child.stderr?.destroy();child.unref();
      if(error)reject(error);else resolve({stdout:stdout!,stderr:''});
    }
    child.once('error',error=>finish(error));
    child.once('exit',()=>finish(Error('Controller exited before its durable launch response; read launch state')));
    child.stdout?.on('data',(chunk:Buffer)=>{
      if(settled)return;bytes=Buffer.concat([bytes,chunk]);
      if(bytes.length>MAX_REPLY){finish(Error('Launch response exceeds its bound; read launch state'));return;}
      const newline=bytes.indexOf(10);if(newline<0)return;
      try{
        const line=new TextDecoder('utf-8',{fatal:true}).decode(bytes.subarray(0,newline));
        if(!line.trim()||bytes.subarray(newline+1).some(value=>![9,10,13,32].includes(value)))throw Error('Unexpected launch response framing');
        JSON.parse(line);finish(undefined,line+'\n');
      }catch{finish(Error('Invalid launch response; read the selected installation launch state'));}
    });
    child.stderr?.on('data',(chunk:Buffer)=>{stderrBytes+=chunk.length;if(stderrBytes>MAX_REPLY)finish(Error('Controller diagnostics exceed their bound; read launch state'));});
  });
}
