"""Lazy native C ABI dispatch for inspection DAGs and reviewable GAN generation."""
import json
from pathlib import Path


def native_create(package_dir,options_json):
    options=json.loads(options_json or '{}')
    manifest=json.loads((Path(package_dir)/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('task')=='defect_gan':
        from backend.engine.gan_package_runtime import GeneratorExecutor
        return GeneratorExecutor(package_dir,**options)
    from backend.engine.flow_package_runtime import native_create as create
    return create(package_dir,options_json)


def native_execute(executor,input_json):
    return json.dumps(executor.execute(json.loads(input_json)),ensure_ascii=False)
