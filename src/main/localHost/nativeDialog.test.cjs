const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const file=path.join(__dirname,'nativeDialog.ts');
test('the owned chooser activates its helper before showing the native panel',async()=>{
 assert.ok(require('node:fs').existsSync(file),'missing owned chooser activation adapter');
 const{chooseNativeDialog}=require('./testSupport.cjs').load(file),events=[],options={title:'Owned scratch chooser',properties:['openDirectory']};
 const result=await chooseNativeDialog({focus:options=>events.push(['focus',options])},{showOpenDialog:async value=>{events.push(['choose',value]);return{canceled:true,filePaths:[]};}},options);
 assert.deepEqual(events,[['focus',{steal:true}],['choose',options]]);assert.deepEqual(result,{canceled:true,filePaths:[]});
});
test('a native chooser failure propagates after activation without retry or cancellation',async()=>{
 assert.ok(require('node:fs').existsSync(file),'missing owned chooser activation adapter');
 const{chooseNativeDialog}=require('./testSupport.cjs').load(file);let calls=0;
 await assert.rejects(chooseNativeDialog({focus(){}},{showOpenDialog:async()=>{calls++;throw Error('synthetic chooser failure');}},{properties:['openFile']}),/synthetic chooser failure/);assert.equal(calls,1);
});
