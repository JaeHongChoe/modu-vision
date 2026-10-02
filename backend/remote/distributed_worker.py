"""Rank process for the real supervised training adapter."""
import argparse
import json
from pathlib import Path
import threading
from backend.engine.distributed_training import initialize_distributed,is_primary,shutdown_distributed


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--spec',type=Path,required=True);args=parser.parse_args(argv)
    spec=json.loads(args.spec.read_text(encoding='utf-8'));run=args.spec.parent
    context=initialize_distributed(spec.get('device') or 'cuda')
    from backend.engine.trainer import UnifiedAutoMLTrainer
    from backend.remote.worker import _StatusWriter,_TrainingStatusCallback,_atomic_json,_read_train_spec,apply_memory_budget
    spec=_read_train_spec(args.spec,run)
    apply_memory_budget({**spec,'device':context.device})
    callback=None
    if is_primary():
        # The parent worker owns status.json; rank zero writes a separate progress
        # document that the launcher relays, avoiding two concurrent status owners.
        callback=_TrainingStatusCallback(_StatusWriter(run/'distributed_progress',spec['job_id']))
    warm_start=None
    if spec.get('warm_start'):
        from backend.engine.warm_start import restore_portable_parent
        warm_start=restore_portable_parent(run,spec['warm_start'],spec['task'])
    trainer=UnifiedAutoMLTrainer(task=spec['task'],dataset_path=run/'input'/'data',output_dir=run/'outputs',warm_start=warm_start,
        preset=spec.get('preset','fast'),device=context.device,config_overrides=spec.get('config_overrides') or {},callback=callback)
    stop=threading.Event()
    def watch():
        while not stop.wait(.05):
            if (run/'cancel').exists():trainer.abort();return
    thread=threading.Thread(target=watch,daemon=True);thread.start()
    try:
        from backend.engine.source_aliases import source_alias_scope
        from backend.remote.snapshot import verify_snapshot_tree
        aliases={}
        if spec.get('source_snapshot'):
            original=spec['source_snapshot'];snapshot=verify_snapshot_tree(run/'source',original['manifest_sha256'])
            aliases[original['canonical_root']]=snapshot
        with source_alias_scope(aliases):result=trainer.train(spec['job_id'])
        if is_primary():_atomic_json(run/'outputs'/'distributed_result.json',result)
        return 0 if result.get('status')=='completed' else 3
    finally:stop.set();thread.join(timeout=1);shutdown_distributed()


if __name__=='__main__':raise SystemExit(main())
