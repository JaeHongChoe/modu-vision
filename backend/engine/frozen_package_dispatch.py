"""Run verified exported source using frozen dependencies, before studio imports.

The exported backend namespace is loaded from its checksum-bound files. Importing
studio flow_package_runtime here would turn parity into a repeated studio run.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import runpy
import sys


def _sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def verify_export_files(package,expected):
    raw=Path(package).expanduser()
    if raw.is_symlink():raise ValueError('Exported package cannot be linked')
    root=raw.resolve(strict=True)
    manifest_path=root/'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file():raise ValueError('Missing unlinked package manifest')
    if not re.fullmatch('[0-9a-f]{64}',expected) or _sha(manifest_path)!=expected:
        raise ValueError('Exported manifest checksum does not match trusted invocation')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('schema_version')!=1 or not isinstance(manifest.get('files'),list):raise ValueError('Invalid exported manifest')
    listed=set()
    for row in manifest['files']:
        relative=row.get('path')
        if not isinstance(relative,str) or '\\' in relative:raise ValueError('Invalid exported file path')
        parts=PurePosixPath(relative)
        if parts.is_absolute() or not parts.parts or any(part in ('.','..') for part in parts.parts) or relative in listed:
            raise ValueError('Unsafe or duplicate exported file path')
        file=root.joinpath(*parts.parts)
        if any((root.joinpath(*parts.parts[:index])).is_symlink() for index in range(1,len(parts.parts)+1)):
            raise ValueError('Exported files cannot follow links')
        if not file.is_file() or file.stat().st_size!=row.get('size') or _sha(file)!=row.get('sha256'):
            raise ValueError(f'Exported file checksum differs: {relative}')
        listed.add(relative)
    for required in ('run_flow.py','pipeline.json','backend/__init__.py','backend/engine/__init__.py','backend/engine/flow_package_runtime.py'):
        if required not in listed:raise ValueError(f'Missing checksum-bound exported runtime: {required}')
    for file in (root/'backend').rglob('*.py'):
        if str(file.relative_to(root)) not in listed:raise ValueError('Unlisted exported Python source')
    return root


def _vendor_namespace(root):
    # This process is dedicated to one package. Remove the trusted helper's
    # import namespace so no already-imported app engine can satisfy vendor imports.
    for name in list(sys.modules):
        if name=='backend' or name.startswith('backend.'):del sys.modules[name]
    sys.path.insert(0,str(root))
    for name,directory in [('backend',root/'backend'),('backend.engine',root/'backend'/'engine')]:
        spec=importlib.util.spec_from_file_location(name,directory/'__init__.py',submodule_search_locations=[str(directory)])
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module
        spec.loader.exec_module(module)
        if '.' in name:setattr(sys.modules['backend'],'engine',module)


def main(arguments=None,*,worker=False):
    parser=argparse.ArgumentParser(description='Execute a checksum-bound exported flow with frozen dependencies')
    parser.add_argument('--package',required=True,type=Path)
    parser.add_argument('--manifest-sha256',required=True)
    parser.add_argument('--output',required=True,type=Path)
    if worker:parser.add_argument('--request',required=True,type=Path)
    else:
        parser.add_argument('--image',required=True,type=Path);parser.add_argument('--device',required=True)
        parser.add_argument('--image-id');parser.add_argument('--deadline-ms',type=int);parser.add_argument('--cpu-threads',type=int)
    args=parser.parse_args(arguments)
    try:
        root=verify_export_files(args.package,args.manifest_sha256)
        output=args.output.expanduser().resolve()
        if output.is_relative_to(root):raise ValueError('Runner output must be outside the immutable package')
        _vendor_namespace(root)
        if worker:
            sys.argv=['flow-package-worker',str(root),str(args.request.resolve(strict=True)),str(output)]
            runtime=importlib.import_module('backend.engine.flow_package_runtime')
            if not Path(runtime.__file__).resolve().is_relative_to(root):raise ValueError('Exported worker import escaped the package')
            runtime.worker_main()
            return 0
        sys.argv=[str(root/'run_flow.py'),'--image',str(args.image),'--device',args.device,'--output',str(output)]
        for flag,value in [('--image-id',args.image_id),('--deadline-ms',args.deadline_ms),('--cpu-threads',args.cpu_threads)]:
            if value is not None:sys.argv.extend([flag,str(value)])
        runpy.run_path(str(root/'run_flow.py'),run_name='__main__')
        return 0
    except (ValueError,OSError,json.JSONDecodeError) as exc:
        parser.exit(2,f'Exported runtime error: {exc}\n')
