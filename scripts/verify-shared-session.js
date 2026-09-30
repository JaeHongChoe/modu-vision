const assert=require('node:assert/strict');
const session=require('../dist-electron/main/sharedSession.js');
(async()=>{
  assert.throws(()=>session.validatedServerUrl('http://gpu.internal:8000'));
  assert.throws(()=>session.validatedServerUrl('https://user:password@server.test'));
  assert.throws(()=>session.validatedServerUrl('https://server.test/redirect?next=another'));
  assert.equal(session.validatedServerUrl('http://127.0.0.1:8512'),'http://127.0.0.1:8512');
  const calls=[];global.fetch=async(url,options)=>{calls.push([url,options]);return {ok:true,json:async()=>({token:'private-token-never-returned',expires_at:Date.now()/1000+300,user:{id:'user1',username:'reviewer',administrator:false}})};};
  const result=await session.loginSharedServer({server_url:'https://server.test',username:'reviewer',password:'long password 123'});
  assert.equal(calls[0][1].redirect,'error');assert.equal(result.token,undefined);
  assert.equal(session.sharedHeaders('https://server.test/api/project/current').Authorization,'Bearer private-token-never-returned');
  assert.deepEqual(session.sharedHeaders('https://other.test/api/project/current'),{});
  assert.deepEqual(session.sharedHeaders('https://server.test.evil.test/api/project/current'),{});
  assert.deepEqual(session.sharedHeaders('https://server.test/public'),{});
  session.selectSharedProject('project1');assert.equal(session.sharedHeaders('wss://server.test/ws/telemetry')['X-Vision-Project'],'project1');
  await session.disconnectSharedServer();assert.equal(session.getSharedConnection(),null);assert.deepEqual(session.sharedHeaders('https://server.test/api/project/current'),{});
  console.log('Shared origin binding, token privacy, redirect rejection and disconnect: passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
