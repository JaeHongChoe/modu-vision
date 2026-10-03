const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
// The training screen's log lists the current job's start, each finished epoch and how it ended (app-flow QA finding).
function load(){const name=path.join(__dirname,'trainingLog.ts'),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);
 m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const base={jobId:'job_1',status:'running',jobPhase:'running',totalEpochs:12,lossHistory:[],startError:null,jobStatusError:null,stopError:null,bestMetric:null};
test('the log shows the job, each finished epoch with its losses, and how the job ended',()=>{const {trainingLogLines}=load();
 assert.deepEqual(trainingLogLines({...base,jobId:null}),[],'nothing before a job');
 const running=trainingLogLines({...base,lossHistory:[{epoch:1,trainLoss:0.91234,valLoss:0.8},{epoch:2,trainLoss:0.5,valLoss:null}]});
 assert.deepEqual(running,['작업 job_1 시작','epoch 1/12 · 학습 손실 0.9123 · 검증 손실 0.8000','epoch 2/12 · 학습 손실 0.5000']);
 const done=trainingLogLines({...base,status:'completed',jobPhase:'completed',bestMetric:0.12345,lossHistory:[{epoch:1,trainLoss:0.4,valLoss:0.3}]});
 assert.equal(done.at(-1),'학습 완료 · 저장 지표 0.1235');
 assert.equal(trainingLogLines({...base,status:'failed',jobStatusError:'out of memory'}).at(-1),'학습 실패 · out of memory');
 assert.equal(trainingLogLines({...base,status:'aborted'}).at(-1),'학습 중단됨');
 assert.ok(trainingLogLines({...base,jobPhase:'preparing',status:'preparing'}).includes('단계 · preparing'));
 assert.deepEqual(trainingLogLines({...base,jobId:null,startError:'dataset missing'}),['시작 실패 · dataset missing']);
 assert.equal(trainingLogLines({...base,stopError:'timeout'}).at(-1),'중단 요청 확인 실패 · timeout');});
