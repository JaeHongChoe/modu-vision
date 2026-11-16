/** Panel-owned saved-state reads. Never approves, resumes, polls or executes a task. */
export interface SavedTaskRestoreSnapshot {authority:string;context:string;ready:boolean;blocked:boolean}
export class SavedTaskRestore {
  private intent:{authority:string;attempted:string|null}|null=null;
  private queued=false;
  private reading=false;
  constructor(private readonly deps:{snapshot():SavedTaskRestoreSnapshot;restore():Promise<unknown>}){}
  start():()=>void{
    const intent={authority:this.deps.snapshot().authority,attempted:null};
    this.intent=intent;this.notify();
    return()=>{if(this.intent===intent)this.stop();};
  }
  stop():void{this.intent=null;}
  notify():void{
    if(!this.intent||this.queued)return;
    this.queued=true;
    queueMicrotask(()=>{this.queued=false;void this.readCurrent();});
  }
  private async readCurrent():Promise<void>{
    const intent=this.intent;if(!intent||this.reading)return;
    const current=this.deps.snapshot();
    if(current.authority!==intent.authority){this.stop();return;}
    if(!current.ready||current.blocked||intent.attempted===current.context)return;
    intent.attempted=current.context;this.reading=true;
    try{await this.deps.restore();}
    catch{/* The controller retains the current read error and its explicit retry. */}
    finally{
      this.reading=false;
      // A context-fenced reply may leave the card empty. Re-read at most once
      // for the latest token; an unchanged empty ledger never becomes a loop.
      this.notify();
    }
  }
}
