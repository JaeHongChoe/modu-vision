"""Prepare owned pack input; optional real provider assets stay inert."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from backend.engine.runtime_pack import canonical_bytes, inventory_pack, read_record
from backend.engine.runtime_pack_store import observed_target

project=Path(sys.argv[1]).absolute()
incoming=project/'runtime-pack-input';incoming.mkdir()
source=incoming/'payload';source.mkdir()
provider=os.environ.get('MV_E2E_RUNTIME_PACK_DIR')
if provider:
    provider=Path(provider).absolute()
    raw=read_record(provider/'inventory.json')
    pin=os.environ.get('MV_E2E_RUNTIME_PACK_SHA256')
    if not pin or hashlib.sha256(raw).hexdigest()!=pin:
        raise ValueError('Explicit reviewed provider inventory pin is required')
    manifest=json.loads(raw)
    # Existing separately reviewed pack assets only; no network, import or pip.
    for row in manifest['files']:
        relative=row['path'];original=provider/'source'/relative
        if any(p.is_symlink() for p in (original,*original.parents)):
            raise ValueError('Provider fixture cannot be linked')
        if not original.resolve().is_relative_to(provider/'source'):
            raise ValueError('Provider fixture path escaped its reviewed source')
        destination=source/relative;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(original,destination)
    label='reviewed_provider_asset_copy_no_activation'
else:
    (source/'never_execute.py').write_bytes(b'raise AssertionError("Inactive pack must never execute")\n')
    target=observed_target()
    manifest=inventory_pack(source,{'id':'first-party-inactive-control','version':'1.0.0','kind':'ocr',
        'platform':target['platform'],'arch':target['arch'],'source':'https://example.invalid/first-party-control',
        'license':'MIT','driver_minimum':None,'compatibility':target['compatibility'],'files':['never_execute.py']})
    raw=canonical_bytes(manifest);pin=hashlib.sha256(raw).hexdigest();label='nonexecuted_first_party_control'
document=incoming/'inventory.json';document.write_bytes(raw)
print(json.dumps({'source_dir':str(source),'inventory_path':str(document),'expected_sha256':pin,
    'id':manifest['id'],'version':manifest['version'],'total_bytes':manifest['total_bytes'],
    'files':manifest['files'],'fixture_scope':label}))
