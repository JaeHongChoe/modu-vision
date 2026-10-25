"""Unbound passive diagnostics. No model imports, entrypoint, launches or flag setters.

The sole execution boundary is CaptureSession.observe(the_original_required_call).
It invokes that supplied callable once, preserving its object/exception identity.
Root must independently bind the installed runtime and original required call.
"""
from __future__ import annotations

import base64
import dis
import hashlib
import importlib.metadata
import json
import math
import marshal
import os
import pathlib
import stat
import sys
import threading
import types
import zlib
from dataclasses import dataclass


def _message(value):
    try:
        return str(value)[:512]
    except BaseException:
        return '<unprintable original exception>'


@dataclass(frozen=True)
class FunctionSpec:
    role: str
    function: object
    fields: tuple[str, ...]

    def __post_init__(self):
        if not isinstance(getattr(self.function, '__code__', None), types.CodeType):
            raise ValueError('Only an original Python producer function can be observed')
        if not self.role or any(not isinstance(v, str) for v in self.fields):
            raise ValueError('Invalid function capture specification')


class CaptureSession:
    """One thread, one required call, exact original code objects, private evidence.

    Capture failures are diagnostic failures, never producer failures. Existing
    trace functions are refused before calling the producer; no Torch hooks or
    production functions are patched. The stdlib control suite uses fake values.
    """
    def __init__(self, output, functions, array_adapter, runtime_probe, *,
                 max_blob_bytes=8 * 1024 * 1024, max_total_bytes=32 * 1024 * 1024,
                 max_events=16, max_depth=12, provenance=None):
        self.output = pathlib.Path(output)
        self.specs = tuple(functions)
        codes = [v.function.__code__ for v in self.specs]
        if not self.specs or len({id(code) for code in codes}) != len(codes):
            raise ValueError('Nonempty distinct exact producer code objects required')
        if not 0 < max_blob_bytes <= max_total_bytes <= 32 * 1024 * 1024:
            raise ValueError('Capture byte bounds exceed the private packet contract')
        if not 0 < max_events <= 16 or not 0 < max_depth <= 12:
            raise ValueError('Capture structure bounds exceed the private packet contract')
        self.by_code = {id(code): spec for code, spec in zip(codes, self.specs)}
        self.adapter, self.probe = array_adapter, runtime_probe
        self.max_blob, self.max_total = max_blob_bytes, max_total_bytes
        self.max_events, self.max_depth = max_events, max_depth
        self.provenance = provenance or {'ready': False, 'actual_binding': None}
        self.events, self.errors, self.active = [], [], {}
        self.bytes_written = 0
        self.bytes_reserved = 0
        self.blob_count = 0
        self.producer_exception = None
        self.observer_removed = False
        self.terminal_error = None
        self.used = False
        self.callback = self._trace

    def _error(self, operation, exc):
        if len(self.errors) < 32:
            self.errors.append({'operation': operation, 'type': type(exc).__name__,
                                'message': _message(exc)})

    def _blob(self, raw, metadata):
        if not isinstance(raw, bytes):
            raise ValueError('Snapshot adapter must return immutable bytes')
        if len(raw) > self.max_blob or self.bytes_reserved + len(raw) > self.max_total:
            raise ValueError('Private capture byte limit exceeded')
        if self.blob_count >= 256:
            raise ValueError('Private capture blob limit exceeded')
        name = f'blob-{self.blob_count:03d}.bin'
        self.blob_count += 1
        # Reserve the entire attempt before I/O: partial/failed writes must not
        # create uncounted output capacity for later observations.
        self.bytes_reserved += len(raw)
        fd = os.open(self.output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            remaining = memoryview(raw)
            while remaining:
                n = os.write(fd, remaining)
                if n <= 0:
                    raise OSError('Capture write made no progress')
                remaining = remaining[n:]
            os.fsync(fd)
        finally:
            os.close(fd)
        self.bytes_written += len(raw)
        return {**metadata, 'file': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}

    def _raster(self, value):
        dtype, shape = value.get('dtype'), value.get('shape')
        if dtype not in ('float32', 'uint8') or not isinstance(shape, list) or len(shape) != 2:
            raise ValueError('Unsupported encoded probability/mask raster')
        if any(type(v) is not int or v < 1 for v in shape):
            raise ValueError('Invalid encoded raster shape')
        expected = math.prod(shape) * (4 if dtype == 'float32' else 1)
        if expected > self.max_blob or not isinstance(value.get('data'), str) or len(value['data']) > self.max_blob * 2:
            raise ValueError('Encoded raster exceeds private capture bound')
        compressed = base64.b64decode(value['data'], validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, expected + 1)
        if len(raw) != expected or not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
            raise ValueError('Encoded raster body/shape mismatch')
        return self._blob(raw, {'kind': 'encoded-raster-decoded', 'dtype': dtype, 'shape': shape,
                                'byte_order': sys.byteorder, 'encoding': 'raw-C',
                                'original_encoding': 'zlib_base64',
                                'encoded_ascii_sha256': hashlib.sha256(value['data'].encode('ascii')).hexdigest()})

    def _snapshot(self, value, depth=0):
        if depth > self.max_depth:
            raise ValueError('Private capture nesting limit exceeded')
        array = self.adapter(value)
        if array is not None:
            metadata = {key: array[key] for key in ('shape', 'dtype', 'device', 'byte_order', 'strides')}
            return self._blob(array['raw'], {'kind': 'array', 'encoding': 'raw-C', **metadata})
        if isinstance(value, dict):
            if value.get('encoding') == 'zlib_base64':
                return self._raster(value)
            if len(value) > 64 or any(not isinstance(k, (str, int)) for k in value):
                raise ValueError('Private capture dictionary limit/key contract exceeded')
            return {str(k): self._snapshot(v, depth + 1) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            if len(value) > 64:
                raise ValueError('Private capture sequence limit exceeded')
            return [self._snapshot(v, depth + 1) for v in value]
        if value is None or type(value) in (str, bool, int, float):
            if isinstance(value, str) and len(value) > 8192:
                raise ValueError('Private capture string limit exceeded')
            return value
        raise ValueError('Unsupported capture value type: ' + type(value).__name__)

    def _safe_snapshot(self, value, operation):
        try:
            return self._snapshot(value)
        except BaseException as exc:
            self._error(operation, exc)
            return {'capture_failed': True, 'type': type(exc).__name__}

    def _runtime(self, operation):
        try:
            return self._safe_snapshot(self.probe(), operation)
        except BaseException as exc:
            self._error(operation, exc)
            return {'capture_failed': True, 'type': type(exc).__name__}

    def _trace(self, frame, event, arg):
        # Tracing is current-thread only. No frame objects survive the call.
        if threading.get_ident() != self.thread_id or id(frame.f_code) not in self.by_code:
            return None
        try:
            if event == 'call':
                if len(self.events) >= self.max_events:
                    raise ValueError('Private capture event limit exceeded')
                spec = self.by_code[id(frame.f_code)]
                row = {'role': spec.role, 'code_filename': frame.f_code.co_filename,
                       'first_line': frame.f_code.co_firstlineno, 'returned': False,
                       'runtime_before': self._runtime('runtime_before'),
                       'inputs': {key: self._safe_snapshot(frame.f_locals[key], spec.role + '.' + key)
                                  for key in spec.fields}, 'return': None, 'exceptions': []}
                self.events.append(row)
                self.active[id(frame)] = row
            elif id(frame) in self.active:
                row = self.active[id(frame)]
                if event == 'exception':
                    if len(row['exceptions']) >= 8:
                        raise ValueError('Private per-frame exception event limit exceeded')
                    row['exceptions'].append({'type': arg[0].__name__, 'message': _message(arg[1])})
                elif event == 'return':
                    # A trace return(None) also denotes exception unwinding. Only
                    # actual RETURN opcodes mean that a producer returned a value.
                    opcode = dis.opname[frame.f_code.co_code[frame.f_lasti]]
                    row['returned'] = opcode in ('RETURN_VALUE', 'RETURN_CONST')
                    row['return_opcode'] = opcode
                    if row['returned']:
                        row['return'] = self._safe_snapshot(arg, row['role'] + '.return')
                    row['runtime_after'] = self._runtime('runtime_after')
                    del self.active[id(frame)]
        except BaseException as exc:
            self._error('trace.' + event, exc)
        return self.callback

    def _finish(self):
        missing = sorted({s.role for s in self.specs} - {v['role'] for v in self.events})
        complete = not self.errors and not missing and not self.active and self.producer_exception is None and all(v['returned'] for v in self.events)
        manifest = {'schema': 'modu-vision.required-app-passive-capture/v1',
                    'ready': False, 'provenance': self.provenance,
                    'thread_id': self.thread_id, 'events': self.events,
                    'runtime_before': self.runtime_before, 'runtime_after': self.runtime_after,
                    'capture_errors': self.errors, 'missing_roles': missing,
                    'capture_complete': complete and self.observer_removed,
                    'producer_exception': self.producer_exception,
                    'observer_removed': self.observer_removed,
                    'unclosed_observed_frames': len(self.active),
                    'bytes_written': self.bytes_written, 'bytes_reserved': self.bytes_reserved,
                    'blob_count': self.blob_count,
                    'actual_installed_runtime_accepted': False, 'historical_CPU_cause_proved': False,
                    'model_quality_approved': False, 'parent_credit': 0}
        raw = (json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
        if len(raw) > 1024 * 1024:
            raise ValueError('Private manifest exceeds 1 MiB')
        fd = os.open(self.output / 'capture.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            remaining = memoryview(raw)
            while remaining:
                n = os.write(fd, remaining)
                if n <= 0:
                    raise OSError('Manifest write made no progress')
                remaining = remaining[n:]
            os.fsync(fd)
        finally:
            os.close(fd)
        self.active.clear()

    def observe(self, original_required_call):
        if self.used:
            raise RuntimeError('A capture session can observe only one required invocation')
        previous = sys.gettrace()
        if previous is not None:
            raise RuntimeError('Refuse to replace an existing trace observer')
        for parent in (*reversed(self.output.parent.parents), self.output.parent):
            if stat.S_ISLNK(parent.lstat().st_mode):
                raise ValueError('Private capture output ancestor is a symlink')
        self.output.mkdir(mode=0o700)
        self.used = True
        self.thread_id = threading.get_ident()
        self.runtime_before = self._runtime('runtime_before_call')
        try:
            sys.settrace(self.callback)
            try:
                return original_required_call()
            except BaseException as exc:
                self.producer_exception = {'type': type(exc).__name__, 'message': _message(exc)}
                raise
        finally:
            try:
                current = sys.gettrace()
                if current is self.callback:
                    sys.settrace(previous)
                elif current is not previous:
                    raise RuntimeError('Another observer replaced the owned trace; leave it untouched')
                self.observer_removed = sys.gettrace() is previous
            except BaseException as exc:
                self._error('observer_cleanup', exc)
            self.runtime_after = self._runtime('runtime_after_call')
            try:
                self._finish()
            except BaseException as exc:
                # Persistence failure is exposed in memory; it never changes the
                # original return value or replaces its exception.
                self.terminal_error = {'type': type(exc).__name__, 'message': _message(exc)}
                self.active.clear()


def production_array_adapter(torch, numpy):
    """Uses already-loaded modules; makes CPU copies, never modifies source values."""
    def adapter(value):
        if isinstance(value, torch.Tensor):
            if math.prod(value.shape) * value.element_size() > 8 * 1024 * 1024:
                raise ValueError('Tensor exceeds private CPU copy bound')
            array = value.detach().cpu().contiguous().numpy()
            return {'raw': array.tobytes(order='C'), 'shape': list(value.shape),
                    'dtype': str(value.dtype), 'device': str(value.device),
                    'byte_order': sys.byteorder, 'strides': list(value.stride())}
        if isinstance(value, numpy.ndarray):
            if math.prod(value.shape) * value.dtype.itemsize > 8 * 1024 * 1024:
                raise ValueError('Array exceeds private byte copy bound')
            return {'raw': value.tobytes(order='C'), 'shape': list(value.shape),
                    'dtype': str(value.dtype), 'device': 'cpu',
                    'byte_order': value.dtype.byteorder, 'strides': list(value.strides)}
        return None
    return adapter


def runtime_probe(torch, cv2=None):
    """Read configured state only; API availability errors remain explicit."""
    def read(call):
        try:
            return call()
        except Exception as exc:
            return {'unavailable': type(exc).__name__, 'message': _message(exc)[:256]}
    def attr(path):
        result = torch
        for key in path.split('.'):
            result = getattr(result, key)
        return result
    flags = {}
    for path in ('backends.cuda.matmul.allow_tf32', 'backends.cudnn.allow_tf32',
                 'backends.cudnn.enabled', 'backends.cudnn.benchmark',
                 'backends.cudnn.deterministic', 'backends.mkldnn.enabled',
                 'backends.mkldnn.deterministic', 'backends.mkldnn.fp32_precision',
                 'backends.cpu.fp32_precision', 'backends.cuda.matmul.fp32_precision',
                 'backends.cudnn.conv.fp32_precision'):
        flags[path] = read(lambda p=path: attr(p))
    for path in ('get_float32_matmul_precision', 'get_default_dtype', 'get_num_threads',
                 'get_num_interop_threads', 'are_deterministic_algorithms_enabled',
                 'is_grad_enabled', 'backends.cuda.flash_sdp_enabled',
                 'backends.cuda.mem_efficient_sdp_enabled', 'backends.cuda.math_sdp_enabled',
                 'backends.cuda.cudnn_sdp_enabled', 'backends.mkl.is_available',
                 'backends.mkldnn.is_available', 'backends.cudnn.version', '__config__.show'):
        flags[path] = read(lambda p=path: str(attr(p)()) if p == 'get_default_dtype' else attr(p)())
    flags['autocast_cpu'] = read(lambda: torch.is_autocast_enabled('cpu'))
    flags['autocast_cuda'] = read(lambda: torch.is_autocast_enabled('cuda'))
    versions = {name: read(lambda n=name: importlib.metadata.version(n))
                for name in ('torch', 'torchvision', 'numpy', 'Pillow', 'opencv-python', 'opencv-python-headless')}
    versions['loaded_torch'] = str(getattr(torch, '__version__', 'unknown'))
    versions['loaded_cuda'] = read(lambda: torch.version.cuda)
    versions['loaded_opencv'] = str(getattr(cv2, '__version__', 'not supplied'))
    return {'python': sys.version, 'byte_order': sys.byteorder, 'versions': versions,
            'flags': flags, 'environment': {key: os.environ.get(key) for key in
              ('NVIDIA_TF32_OVERRIDE', 'TORCH_ALLOW_TF32_CUBLAS_OVERRIDE', 'CUBLAS_WORKSPACE_CONFIG',
               'ONEDNN_DEFAULT_FPMATH_MODE', 'DNNL_DEFAULT_FPMATH_MODE', 'MKL_CBWR',
               'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS')}}


def producer_specs(rotation, model_backbones, segmentation_evidence):
    """Original function references only; no rebinding, patching or function calls."""
    return (
        FunctionSpec('rotation_forward', rotation.RotationNet.forward, ('images',)),
        FunctionSpec('segmentation_forward', model_backbones.DinoTaskModel.forward, ('x',)),
        FunctionSpec('rotation_predict', rotation._predict, ('rgb', 'image_size')),
        FunctionSpec('class_evidence', segmentation_evidence.class_evidence,
                     ('probabilities', 'pixels', 'bbox', 'names', 'threshold', 'params')),
        FunctionSpec('remap_class_evidence', segmentation_evidence.remap_class_evidence,
                     ('entries', 'transform', 'source_shape', 'bbox', 'source_pixels', 'params')),
    )


def verify_bound_functions(functions, source_root, source_pins):
    """Pure source admission helper; never imports/executes the source being bound.

    Root still supplies installer/runtime/input/checkpoint/required-call authority.
    A readable exact source plus equal original function code is required here;
    compiled/native producers fail explicitly rather than being called or patched.
    """
    root = pathlib.Path(source_root)
    if not root.is_absolute() or root.resolve(strict=True) != root:
        raise ValueError('A canonical actual source root is required')
    files, compiled = {}, {}
    def walk(code):
        yield code
        for item in code.co_consts:
            if isinstance(item, types.CodeType):
                yield from walk(item)
    def pinned_source(relative, expected):
        part = pathlib.Path(relative)
        if part.is_absolute() or '..' in part.parts:
            raise ValueError('Source pin leaves the exact source root')
        path = root / part
        if path.resolve(strict=True) != path:
            raise ValueError('Source pin traverses a symbolic link')
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Only regular original Python source can be bound')
        raw = path.read_bytes()
        after = path.lstat()
        identity = lambda s: [s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_uid, s.st_gid]
        digest = hashlib.sha256(raw).hexdigest()
        if identity(before) != identity(after) or digest != expected['sha256'] or len(raw) != expected['size']:
            raise ValueError('Exact producer source bytes changed')
        row = {'path': str(path), 'sha256': digest, 'size': len(raw), 'raw9': identity(after)}
        codes = tuple(walk(compile(raw, str(path), 'exec', dont_inherit=True, optimize=sys.flags.optimize)))
        return row, codes
    for relative, expected in source_pins.items():
        files[relative], compiled[str(root / relative)] = pinned_source(relative, expected)
    roles, observer_files = {}, {}
    for spec in functions:
        code = spec.function.__code__
        ordinary = code.co_filename in compiled and any(candidate == code for candidate in compiled[code.co_filename])
        if (spec.role == 'original_required_execute'
                and (source_pins == CURRENT_PRODUCER_SOURCE_PINS
                     or source_pins == COMBINED_PRODUCER_SOURCE_PINS
                     or source_pins == PARAMETERLESS_ROTATION_PRODUCER_SOURCE_PINS
                     or source_pins == COLD_SCHEMA_PRODUCER_SOURCE_PINS)):
            # Current execute must remain the registered observer even when an
            # unwrapped original engine code object is otherwise source-pinned.
            relative = 'backend/engine/original_owned_flow_execution_request.py'
            row, codes = pinned_source(relative, REGISTERED_ENGINE_OBSERVER_PIN)
            _verify_registered_engine_wrapper(spec.function, root, compiled, codes)
            observer_files[relative] = row
        elif not ordinary:
            raise ValueError('Loaded original producer code differs from pinned Python source')
        roles[spec.role] = {'path': code.co_filename, 'first_line': code.co_firstlineno,
                            'code_sha256_this_runtime_only': hashlib.sha256(marshal.dumps(code)).hexdigest()}
    proof = {'files': files, 'roles': roles, 'actual_installer_runtime_or_required_call_accepted': False}
    if observer_files: proof['observer_files'] = observer_files
    return proof


def _verify_registered_engine_wrapper(function, root, compiled, observer_codes):
    """Only the real registered current engine wrapper, its original closure/code.

    The modules must already be loaded by the original caller. This source-only
    admission helper imports or invokes no application producer or decorator.
    """
    engine_module = sys.modules.get('backend.engine.flowchart_engine')
    observer_module = sys.modules.get('backend.engine.original_owned_flow_execution_request')
    if type(engine_module) is not types.ModuleType or type(observer_module) is not types.ModuleType:
        raise ValueError('Original loaded engine and observer modules required')
    engine_class = vars(engine_module).get('FlowchartEngine')
    decorated = vars(observer_module).get('_DECORATED')
    factory = vars(observer_module).get('observe_original_execution_effect')
    observer_path = str(root / 'backend/engine/original_owned_flow_execution_request.py')
    engine_path = str(root / 'backend/engine/flowchart_engine.py')
    original = getattr(function, '__wrapped__', None)
    if (type(function) is not types.FunctionType or type(engine_class) is not type
            or vars(engine_class).get('execute') is not function
            or type(decorated) is not dict or decorated.get('engine') is not function
            or function.__globals__ is not vars(observer_module)
            or function.__code__.co_filename != observer_path
            or not any(code == function.__code__ for code in observer_codes)
            or type(factory) is not types.FunctionType or factory.__globals__ is not vars(observer_module)
            or not any(code == factory.__code__ for code in observer_codes)
            or type(original) is not types.FunctionType or original.__globals__ is not vars(engine_module)
            or original.__code__.co_filename != engine_path
            or not any(code == original.__code__ for code in compiled.get(engine_path, ()))
            or function.__code__.co_freevars != ('function', 'kind')
            or function.__closure__ is None or len(function.__closure__) != 2):
        raise ValueError('Only the exact registered original engine observer is allowed')
    closure = dict(zip(function.__code__.co_freevars, (cell.cell_contents for cell in function.__closure__)))
    if closure['kind'] != 'engine' or closure['function'] is not original:
        raise ValueError('Original engine observer closure differs')


# Narrow opt-in adapter for the one retained required selected-worker flow.
from dataclasses import field as _grant_field

PRODUCER_SOURCE_PINS = types.MappingProxyType({name:types.MappingProxyType(row) for name,row in {'backend/engine/flow_operators.py': {'size': 10481, 'sha256': '5b0c54aa9edb8e380747fbbd027d7bfa497ec51d02dc3e0a9628e4b87b5f688c'}, 'backend/engine/flow_package_runtime.py': {'size': 35583, 'sha256': 'a55ca0b694d398bebe30af4916ccd43e0ab7ea115a9ae080af3bc313abcfeeec'}, 'backend/engine/flowchart_engine.py': {'size': 148262, 'sha256': 'e62c8461a0f57d4ce8b4e49f5a0dbf21f50e25515e627f5cff778225cdcf1a70'}, 'backend/engine/model_backbones.py': {'size': 19191, 'sha256': '6873fecb60f4969bf4e15fbb057cebe1100b57c34e0685148b1fcd309d2192cf'}, 'backend/engine/rotation.py': {'size': 20447, 'sha256': 'b02622ed1c444272950b5f1f52523a29df096d71b8f257fc27903e380a492b45'}, 'backend/engine/segmentation_evidence.py': {'size': 8992, 'sha256': 'a25ff47aab794f516d8573c66d3a7d624368b1835c1baa23357bacd1efc207d4'}}.items()})
# Exact source successor: the reviewed engine observer import/decorator only.
CURRENT_PRODUCER_SOURCE_PINS = types.MappingProxyType({name: types.MappingProxyType(dict(row))
    for name, row in {**PRODUCER_SOURCE_PINS, 'backend/engine/flowchart_engine.py': {
        'size': 148410, 'sha256': '00da5942fd90a37ea8cb5a86e89aed9ac3c65026f9e50b9337a42019c5acf001'},
        'backend/engine/flow_package_runtime.py': {'size': 36196,
            'sha256': 'c118527a396694b3bb2e5cfa237f174e9d6b9011c5146c13b930a8c17eb83c51'}}.items()})
# Exact semantic successor: accepted local partial-cancel engine, same other five producers.
COMBINED_PRODUCER_SOURCE_PINS = types.MappingProxyType({name: types.MappingProxyType(dict(row))
    for name, row in {**CURRENT_PRODUCER_SOURCE_PINS, 'backend/engine/flowchart_engine.py': {
        'size': 151555, 'sha256': '83bb9b25c7d66f91796cdbe6a83d6c13a3728991d76dd9cf7d3f73a27938f976'}}.items()})
# Separate exact successor: rotation's parameterless-adapter input-device fallback.
# The observed _predict/forward functions and the other five producer sources stay fixed.
PARAMETERLESS_ROTATION_PRODUCER_SOURCE_PINS = types.MappingProxyType({name: types.MappingProxyType(dict(row))
    for name, row in {**COMBINED_PRODUCER_SOURCE_PINS, 'backend/engine/rotation.py': {
        'size': 20800, 'sha256': '6fd1d70540d08263f46d1165dddd3e0ca1071abf2c1723bf2aa7572e9bd82899'}}.items()})
# Separate exact successor: the five unchanged graph classes now live in a cold
# schema module. Both that dependency and its importing engine must be pinned;
# the prior inline-schema cohort remains bound to its original six sources.
COLD_SCHEMA_PRODUCER_SOURCE_PINS = types.MappingProxyType({name: types.MappingProxyType(dict(row))
    for name, row in {**PARAMETERLESS_ROTATION_PRODUCER_SOURCE_PINS,
        'backend/engine/flowchart_engine.py': {'size': 149170,
            'sha256': 'aa2b99d4e8fd2d76e6123f98f89e8fc670b8107139f9ad7571187de85e9e833a'},
        'backend/engine/flowchart_schema.py': {'size': 2873,
            'sha256': '37f10afcb3637ce1222597a695a074724484ea88571d3885914a0155d31e707c'}}.items()})
REGISTERED_ENGINE_OBSERVER_PIN = types.MappingProxyType({'size': 40214,
    'sha256': '9102a6126774d9d68b6097d844dcc5c8984b09fe0ab14b97fd06ce7d4db80fe3'})

RETAINED_IMAGE_SHA = 'a051236ae468207f225554417c744c4606e8bd152d69b420c3a961b02bc4d5cf'
RETAINED_GRAPH_SHA = '914412ee1da1765315f1bb0d82af620bd402fa5eefd22b8ed964793c328df0be'
RETAINED_GPU_UUID = 'GPU-680335ce-4e6c-c68f-e3ee-7135586d4a04'
RETAINED_MODELS = (('1c95798117d2439998ff696638561e75','rotation','4c3c5c99adca1396d79d7217dc33138193cdd8132519e19f8b4cc8d738aabf97'),('job_1791504538_ea1648','segmentation','6843b566058af262ead960cf871a0581dc346a49562c2cb90ad13116500a0b3b'))

def _hex(value,length):
    return type(value)is str and len(value)==length and all(c in '0123456789abcdef'for c in value)


def _graph_sha(value):
    if type(value)is not dict:raise ValueError('Original retained graph must be a dictionary')
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _model_rows(values):
    if type(values)is not list or len(values)!=2 or any(type(v)is not dict for v in values):raise ValueError('Exactly two original retained model references required')
    rows=[]
    for row in values:
        item=(row.get('job_id'),row.get('task'),row.get('checkpoint_sha256'))
        if item not in RETAINED_MODELS:raise ValueError('Original retained model reference differs')
        rows.append(item)
    if sorted(rows)!=sorted(RETAINED_MODELS):raise ValueError('Original retained model set differs')
    return [{'job_id':j,'task':t,'checkpoint_sha256':s}for j,t,s in sorted(rows)]


def _pins_dictionary(cohort=None):
    pins = PRODUCER_SOURCE_PINS if cohort is None else cohort['source_pins']
    return {name:dict(row)for name,row in pins.items()}


@dataclass(frozen=True)
class RequiredFlowCaptureGrant:
    """One-use diagnostic data selector; never worker/device/lease authority.

    Root's reviewed installed startup fixture supplies this exact typed object
    on app.state before the original request. HTTP/JSON callers cannot create it.
    Its real installed startup binding remains a separate Root acceptance gate.
    """
    nonce:str
    profile_id:str
    execution_profile_sha256:str
    _state:object=_grant_field(init=False,repr=False,compare=False)

    def __post_init__(self):
        if not _hex(self.nonce,32)or type(self.profile_id)is not str or not 0<len(self.profile_id)<=128 or not _hex(self.execution_profile_sha256,64):raise ValueError('Invalid fixed required-flow diagnostic selector')
        object.__setattr__(self,'_state',[False,threading.Lock()])


def serialize_required_flow_grant(grant,*,profile_id,execution_profile_sha256,pipeline,image_sha256,models):
    cohort=None
    if type(grant)is _OriginalRequiredFlowRequest:
        grant,cohort=grant.consume(profile_id,execution_profile_sha256,pipeline,image_sha256,models)
    if type(grant)is not RequiredFlowCaptureGrant:raise ValueError('Only the original typed host diagnostic selector is allowed')
    rows=_model_rows(models)
    if cohort is None:
        # A direct typed historical grant keeps its original profile-only tuple.
        cohort=_cohort_for_data(grant.profile_id,grant.execution_profile_sha256)
    if (profile_id!=grant.profile_id or execution_profile_sha256!=grant.execution_profile_sha256
            or _graph_sha(pipeline)!=RETAINED_GRAPH_SHA or image_sha256!=RETAINED_IMAGE_SHA):raise ValueError('Original required flow data/compute selector differs')
    lock=grant._state[1]
    if not lock.acquire(blocking=False):raise ValueError('Original diagnostic selector is already in use')
    try:
        if grant._state[0]:raise ValueError('Original diagnostic selector was already consumed')
        grant._state[0]=True
    finally:lock.release()
    return {'schema':'modu-vision.required-retained-flow-passive-grant/v1','nonce':grant.nonce,'profile_id':profile_id,
            'execution_profile_sha256':execution_profile_sha256,'pipeline_sha256':RETAINED_GRAPH_SHA,
            'image_sha256':RETAINED_IMAGE_SHA,'models':rows,'source_pins':_pins_dictionary(cohort),
            'gpu_uuid':RETAINED_GPU_UUID,'required_call':'FlowchartEngine.execute','runtime_authority':False,'parent_credit':False}


def validate_worker_grant(value,spec):
    fields={'schema','nonce','profile_id','execution_profile_sha256','pipeline_sha256','image_sha256','models','source_pins','gpu_uuid','required_call','runtime_authority','parent_credit'}
    if (type(value)is not dict or set(value)!=fields or value['schema']!='modu-vision.required-retained-flow-passive-grant/v1'
            or not _hex(value['nonce'],32)or type(value['profile_id'])is not str or not 0<len(value['profile_id'])<=128
            or not _hex(value['execution_profile_sha256'],64)
            or value['gpu_uuid']!=RETAINED_GPU_UUID or value['required_call']!='FlowchartEngine.execute'
            or value['runtime_authority']is not False or value['parent_credit']is not False):raise ValueError('Fixed required-flow worker diagnostic data differs')
    _cohort_for_data(value['profile_id'],value['execution_profile_sha256'],value['source_pins'])
    if (type(spec)is not dict or spec.get('operation')!='flowchart_run'or spec.get('portable_models')is not True
            or spec.get('stop_node_id')is not None or spec.get('comparison_operation_contract')is not None
            or spec.get('device')not in ('cuda','cuda:0')
            or spec.get('execution_profile_sha256')!=value['execution_profile_sha256']
            or value['pipeline_sha256']!=RETAINED_GRAPH_SHA or _graph_sha(spec.get('pipeline'))!=RETAINED_GRAPH_SHA
            or value['image_sha256']!=RETAINED_IMAGE_SHA or spec.get('image_sha256')!=RETAINED_IMAGE_SHA
            or _model_rows(value['models'])!=_model_rows(spec.get('models'))):raise ValueError('Original required worker operation scope differs')
    return value


class _PreparedObservation:
    """Captured method/argument references for data observation, no authority flags."""
    def __init__(self,engine,method,kwargs,session):
        if getattr(method,'__self__',None)is not engine or not callable(getattr(method,'__func__',None))or type(kwargs)is not dict or type(session)is not CaptureSession:raise ValueError('Original engine method, arguments and capture session required')
        self.engine,self.method,self.function,self.kwargs,self.session=engine,method,method.__func__,kwargs,session
        self.values=tuple(kwargs.items());self.thread=threading.get_ident();self.used=False
        self.original_session=session;self.record_provenance=None;self.record_nonce=None
        self.function_code=self.function.__code__
        self.producer_codes=tuple((spec.function,spec.function.__code__)for spec in session.specs)

    def observe(self,engine,kwargs):
        if self.used:raise RuntimeError('Original required observation is one-use')
        method=getattr(engine,'execute',None)
        if (self.session is not self.original_session or threading.get_ident()!=self.thread or engine is not self.engine or kwargs is not self.kwargs
                or tuple(kwargs)!=tuple(k for k,v in self.values)or any(kwargs[k]is not v for k,v in self.values)
                or getattr(method,'__self__',None)is not self.engine or getattr(method,'__func__',None)is not self.function
                or self.function.__code__ is not self.function_code or any(f.__code__ is not code for f,code in self.producer_codes)):raise ValueError('Original required call/thread/method/arguments were replaced')
        self.record_provenance=self.session.provenance
        self.record_nonce=self.record_provenance.get('grant_nonce')if type(self.record_provenance)is dict else None
        self.used=True
        return self.session.observe(lambda:self.method(**self.kwargs))


def observe_required_engine_call(engine,kwargs,prepared=None):
    if prepared is None:return engine.execute(**kwargs)
    if type(prepared)is not _PreparedObservation:raise ValueError('Unknown required-flow observation handle')
    return prepared.observe(engine,kwargs)


def _fixed_source_cohort(source_root, expected=None):
    # Complete coupled records only; no independent source/profile lists.
    for cohort in RETAINED_CAPTURE_COHORTS:
        if expected is not None and cohort is not expected: continue
        try:
            return cohort, verify_bound_functions((),source_root,cohort['source_pins'])
        except (OSError,ValueError,SyntaxError):
            continue
    raise ValueError('Fixed original source custody refused')


def _verify_fixed_source_files(source_root):
    return _fixed_source_cohort(source_root)[1]


def _capture_output(run_dir,nonce):
    root=pathlib.Path(run_dir)
    if not _hex(nonce,32)or not root.is_absolute()or root.resolve(strict=True)!=root or root.parent.name!='runs'or not root.name.startswith('op_'):raise ValueError('Original owned operation log directory required')
    for parent in (*root.parents,root):
        if stat.S_ISLNK(parent.lstat().st_mode):raise ValueError('Operation log path contains a symlink')
    row=root.lstat()
    if not stat.S_ISDIR(row.st_mode)or row.st_uid!=os.getuid()or stat.S_IMODE(row.st_mode)&0o022:raise ValueError('Original operation log owner/mode differs')
    for name in ('worker.lock','status.json'):
        marker=root/name
        try:s=marker.lstat()
        except OSError as exc:raise ValueError('Original operation claim/status unavailable')from exc
        if not stat.S_ISREG(s.st_mode)or s.st_nlink!=1 or s.st_uid!=os.getuid()or stat.S_IMODE(s.st_mode)!=0o600:raise ValueError('Original operation claim/status owner differs')
    logs=root/'outputs'
    logs.mkdir(mode=0o700,exist_ok=True)
    ls=logs.lstat()
    if stat.S_ISLNK(ls.st_mode)or not stat.S_ISDIR(ls.st_mode)or ls.st_uid!=os.getuid()or stat.S_IMODE(ls.st_mode)&0o022:raise ValueError('Original owned output/log root differs')
    output=logs/('required-passive-capture-'+nonce)
    if output.exists()or output.is_symlink():raise ValueError('Original required capture output is already used')
    return output


def _observed_selected_gpu_uuid(properties):
    """Observe the same full physical UUID across PyTorch and NVML spellings.

    Preserve the original conversion of torch's CUDA UUID object. Only the
    existing full UUID matcher establishes physical identity; no device is
    selected and missing, different or MIG identities remain unsupported.
    """
    from ..remote.evaluation_cohort import _gpu_uuid_matches
    observed = str(getattr(properties, 'uuid', 'unavailable'))
    if not _gpu_uuid_matches(observed, RETAINED_GPU_UUID):
        raise ValueError('Original selected GPU UUID cannot be observed exactly')
    return 'GPU-' + observed.removeprefix('GPU-').lower()


def prepare_worker_observation(engine,kwargs,value,spec,run_dir,actual_device):
    """Source-only candidate seam; original worker calls it after its own guards.

    It never authorizes a worker, compute selection, checkpoint, installation or
    shutdown. Source/selector refusal happens before the required invocation.
    Fixed relative imports use the already uploaded application package only.
    """
    grant=validate_worker_grant(value,spec)
    root=pathlib.Path(__file__).resolve(strict=True).parents[2]
    cohort=_cohort_for_data(grant['profile_id'],grant['execution_profile_sha256'],grant['source_pins'])
    _fixed_source_cohort(root,cohort)
    # Static known application imports, never arbitrary external source execution.
    from . import rotation,model_backbones,segmentation_evidence
    from .flowchart_engine import FlowchartEngine
    import torch
    import numpy
    import cv2
    method=getattr(engine,'execute',None)
    if (getattr(method,'__self__',None)is not engine or getattr(method,'__func__',None)is not FlowchartEngine.execute
            or type(kwargs)is not dict or set(kwargs)!={'pipeline','image_path','image_id'}
            or _graph_sha(kwargs['pipeline'].model_dump())!=RETAINED_GRAPH_SHA):raise ValueError('Exact original full-flow engine method/arguments required')
    image=pathlib.Path(kwargs['image_path']);run=pathlib.Path(run_dir)
    if (not image.is_absolute()or image.resolve(strict=True)!=image or not image.is_relative_to(run)
            or hashlib.sha256(image.read_bytes()).hexdigest()!=RETAINED_IMAGE_SHA):raise ValueError('Exact original uploaded image differs')
    if str(actual_device)not in ('cuda','cuda:0'):raise ValueError('Original visible selected CUDA device differs')
    properties=torch.cuda.get_device_properties(actual_device)
    observed_uuid=_observed_selected_gpu_uuid(properties)
    functions=producer_specs(rotation,model_backbones,segmentation_evidence)
    source=verify_bound_functions((*functions,FunctionSpec('original_required_execute',FlowchartEngine.execute,())),root,cohort['source_pins'])
    output=_capture_output(run_dir,grant['nonce'])
    configured_runtime=runtime_probe(torch,cv2)
    def probe():
        return {**configured_runtime,'original_selected_device':str(actual_device),'observed_GPU_UUID':observed_uuid}
    operation_binding=original_worker_capture_binding(run_dir,spec)
    provenance={'schema':'modu-vision.required-selected-worker-passive-data/v1','grant_nonce':grant['nonce'],
                'profile_id':grant['profile_id'],'execution_profile_sha256':grant['execution_profile_sha256'],
                'pipeline_sha256':RETAINED_GRAPH_SHA,'image_sha256':RETAINED_IMAGE_SHA,'models':grant['models'],
                'source':source,'operation_binding':operation_binding,'observed_GPU_UUID':observed_uuid,'actual_installed_runtime_accepted':False,
                'worker_ownership_accepted':False,'model_quality_approved':False,'parent_credit':0}
    session=CaptureSession(output,functions,production_array_adapter(torch,numpy),probe,provenance=provenance)
    return _PreparedObservation(engine,method,kwargs,session)


def worker_capture_record(prepared,run_dir):
    """Best-effort evidence only after the original return; never replace it."""
    result={'schema':'modu-vision.required-selected-worker-passive-record/v1','capture_complete':False,
            'files':[],'metadata':{'capture_complete':False,'runtime_authority':False,'parent_credit':False}}
    try:
        if type(prepared)is not _PreparedObservation or not prepared.used:raise ValueError('Original required observation is absent')
        session=prepared.session;root=pathlib.Path(run_dir)
        if (session is not prepared.original_session or type(session)is not CaptureSession
                or session.provenance is not prepared.record_provenance or not _hex(prepared.record_nonce,32)
                or session.provenance.get('grant_nonce')!=prepared.record_nonce):raise ValueError('Original capture session/provenance nonce differs')
        output=session.output
        if output.parent!=root/'outputs' or output.name!='required-passive-capture-'+prepared.record_nonce:raise ValueError('Original private capture output scope differs')
        # Retain the original observed selector before any fallible file read.
        # It marks incomplete diagnostic data, never a foreign session/authority.
        result['metadata']['grant_nonce']=prepared.record_nonce
        if 'operation_binding'in session.provenance:
            result['metadata']['operation_binding']=dict(session.provenance['operation_binding'])
        path=output/'capture.json';s=path.lstat()
        if not stat.S_ISREG(s.st_mode)or s.st_nlink!=1 or s.st_uid!=os.getuid()or stat.S_IMODE(s.st_mode)!=0o600 or s.st_size>1024*1024:raise ValueError('Private capture manifest identity/bound differs')
        raw=path.read_bytes();manifest=json.loads(raw)
        if manifest.get('schema')!='modu-vision.required-app-passive-capture/v1'or manifest.get('actual_installed_runtime_accepted')is not False:raise ValueError('Private capture data schema differs')
        entries=sorted(output.iterdir());allowed={'capture.json',*(f'blob-{i:03d}.bin'for i in range(session.blob_count))}
        if {p.name for p in entries}!=allowed or len(entries)>257:raise ValueError('Unknown private capture data member')
        for p in entries:
            row=p.lstat()
            if not stat.S_ISREG(row.st_mode)or row.st_nlink!=1 or row.st_uid!=os.getuid()or stat.S_IMODE(row.st_mode)!=0o600 or row.st_size==0:raise ValueError('Private capture data member identity differs')
        complete=manifest.get('capture_complete')is True and session.terminal_error is None
        result.update(capture_complete=complete,files=[p.relative_to(root).as_posix()for p in entries],metadata={
            'grant_nonce':session.provenance['grant_nonce'],'capture_complete':complete,
            'manifest_path':path.relative_to(root).as_posix(),'manifest_sha256':hashlib.sha256(raw).hexdigest(),
            'runtime_authority':False,'parent_credit':False})
        if session.terminal_error is not None:result['metadata']['capture_refusal_code']='CAPTURE_PERSISTENCE_INCOMPLETE'
        if 'operation_binding'in session.provenance:
            result['metadata']['operation_binding']=dict(session.provenance['operation_binding'])
    except Exception:
        result['metadata']['capture_refusal_code']='CAPTURE_READBACK_INCOMPLETE'
    return result


def read_downloaded_capture_record(metadata,artifacts,grant):
    """Read authenticated original artifact data; no acceptance is conferred."""
    if (type(metadata)is not dict or metadata.get('grant_nonce')!=grant['nonce']
            or type(metadata.get('capture_complete'))is not bool or metadata.get('runtime_authority')is not False
            or metadata.get('parent_credit')is not False):raise ValueError('Original required passive result metadata differs')
    relative=metadata.get('manifest_path')
    if relative is None:
        if metadata['capture_complete']is False and metadata.get('capture_refusal_code')in ('CAPTURE_PERSISTENCE_INCOMPLETE','CAPTURE_READBACK_INCOMPLETE'):return dict(metadata)
        raise ValueError('Original private capture manifest is missing')
    expected='outputs/required-passive-capture-'+grant['nonce']+'/capture.json'
    if relative!=expected or not _hex(metadata.get('manifest_sha256'),64):raise ValueError('Original private capture manifest path/hash differs')
    path=artifacts.get(relative)
    if not isinstance(path,pathlib.Path)or path.resolve(strict=True)!=path:raise ValueError('Original downloaded capture artifact is unavailable')
    before=path.lstat()
    if not stat.S_ISREG(before.st_mode)or before.st_nlink!=1 or before.st_uid!=os.getuid()or before.st_size>1024*1024:raise ValueError('Downloaded capture artifact identity/bound differs')
    raw=path.read_bytes();after=path.lstat();identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_uid,s.st_gid)
    if identity(before)!=identity(after)or hashlib.sha256(raw).hexdigest()!=metadata['manifest_sha256']:raise ValueError('Downloaded capture artifact body changed')
    value=json.loads(raw)
    if (type(value)is not dict or value.get('schema')!='modu-vision.required-app-passive-capture/v1'
            or type(value.get('capture_complete'))is not bool or value['capture_complete']!=metadata['capture_complete']
            or value.get('actual_installed_runtime_accepted')is not False or value.get('model_quality_approved')is not False
            or type(value.get('parent_credit'))is not int or value['parent_credit']!=0 or type(value.get('capture_errors'))is not list
            or type(value.get('missing_roles'))is not list):raise ValueError('Downloaded passive data schema differs')
    if value['capture_complete']and (value.get('observer_removed')is not True or value.get('unclosed_observed_frames')!=0
            or value['capture_errors']!=[]or value['missing_roles']!=[]or value.get('producer_exception')is not None):raise ValueError('Downloaded passive data is incomplete')
    return {**metadata,'downloaded_manifest':str(path),'runtime_authority':False,'parent_credit':False}


# Exact existing credential-free profile: raw store row and model_dump are
# deliberately distinct; execution uses the original default-ASCII JSON codec.
import secrets as _capture_secrets
RETAINED_PROFILE_ID='server42-l40s-2'
RETAINED_RAW_PROFILE_SHA='dd585cde09789bff266881082522264fe99a2c579122ea0b18f0b74489e5987c'
RETAINED_EXECUTION_PROFILE_SHA='3e0ff9bf6a23791b64b4decd1d51fdbc2af8bb5d4d9ea37b9bc28c5d4c5813a1'

# These profile SHA values use the unchanged original default-ASCII model_dump
# codec. Current ID43/image stay stable across fresh private build namespaces.
PARAMETERLESS_ROTATION_CAPTURE_COHORT = types.MappingProxyType({
    'profile_id': 'server42-l40s-2-native43-20261016-01',
    'execution_profile_sha256': '6f36f621864d3c6b327295f684118434c6c1d18ddee8b21f30071527b0e7ea49',
    'source_pins': PARAMETERLESS_ROTATION_PRODUCER_SOURCE_PINS})
COLD_SCHEMA_CAPTURE_COHORT = types.MappingProxyType({
    'profile_id': 'server42-l40s-2-native43-20261016-01',
    'execution_profile_sha256': '6f36f621864d3c6b327295f684118434c6c1d18ddee8b21f30071527b0e7ea49',
    'source_pins': COLD_SCHEMA_PRODUCER_SOURCE_PINS})
RETAINED_CAPTURE_COHORTS = (
    types.MappingProxyType({'profile_id': RETAINED_PROFILE_ID,
        'execution_profile_sha256': RETAINED_EXECUTION_PROFILE_SHA,
        'source_pins': PRODUCER_SOURCE_PINS}),
    types.MappingProxyType({'profile_id': 'server42-l40s-2-native43-20261016-01',
        'execution_profile_sha256': '6f36f621864d3c6b327295f684118434c6c1d18ddee8b21f30071527b0e7ea49',
        'source_pins': CURRENT_PRODUCER_SOURCE_PINS}),
    types.MappingProxyType({'profile_id': 'server42-l40s-2-native43-20261016-01',
        'execution_profile_sha256': '6f36f621864d3c6b327295f684118434c6c1d18ddee8b21f30071527b0e7ea49',
        'source_pins': COMBINED_PRODUCER_SOURCE_PINS}),
    PARAMETERLESS_ROTATION_CAPTURE_COHORT,
    COLD_SCHEMA_CAPTURE_COHORT,
)


def _cohort_for_data(profile_id, profile_sha, source_pins=None):
    for cohort in RETAINED_CAPTURE_COHORTS:
        if (profile_id == cohort['profile_id'] and profile_sha == cohort['execution_profile_sha256']
                and (source_pins is None or source_pins == _pins_dictionary(cohort))):
            return cohort
    raise ValueError('Exact coupled retained source/profile cohort differs')


class _OriginalRequiredFlowSelector:
    """In-process fixed data selector; no request or worker permission."""
    def __init__(self,app,current_admission,auth_type,source_root):
        self.app,self.state=app,app.state
        self.admission,self.token=app.state.owned_work_admission,app.state.api_token
        self.current_admission=current_admission
        self.admission_code=current_admission.__code__
        self.auth_type,self.auth_call=auth_type,auth_type.__call__
        self.auth_code=self.auth_call.__code__
        self.accounts=self.state.accounts
        self.shared_owner=None
        if self.accounts is not None:
            from backend.engine.original_shared_required_flow import OriginalSharedRequiredFlowOwner
            self.shared_owner=OriginalSharedRequiredFlowOwner(app,self.admission,auth_type)
        self.source_root=pathlib.Path(source_root)
        self.cohort,_=_fixed_source_cohort(self.source_root)
        self.original_cohort=self.cohort
        self.pid=os.getpid();self.used=False;self.lock=threading.Lock();self.issued=None
        self.nonce=_capture_secrets.token_hex(16)

    def current(self,request):
        if (os.getpid()!=self.pid or self.app.state is not self.state
                or self.state.owned_work_admission is not self.admission
                or self.state.api_token is not self.token or self.state.accounts is not self.accounts
                or self.state.required_retained_flow_capture is not self
                or self.cohort is not self.original_cohort
                or not any(self.cohort is row for row in RETAINED_CAPTURE_COHORTS)
                or self.current_admission.__code__ is not self.admission_code
                or self.current_admission()is not self.admission
                or self.auth_type.__call__ is not self.auth_call or self.auth_call.__code__ is not self.auth_code):
            raise ValueError('Original startup data selector binding changed')
        scope=request.scope
        if (request.app is not self.app or type(scope)is not dict or scope.get('app')is not self.app
                or scope.get('type')!='http' or scope.get('method')!='POST'
                or scope.get('path')!='/api/flowchart/run' or scope.get('query_string',b'')!=b''):
            raise ValueError('Original authenticated full-flow request scope differs')
        if self.shared_owner is not None:
            self.shared_owner.current(request)
            return scope  # Original shared authorization replaces desktop-header identity only here.
        headers=scope.get('headers')
        if not isinstance(headers,(list,tuple)):raise ValueError('Original process-token headers unavailable')
        tokens=[v for k,v in headers if type(k)is bytes and k.lower()==b'x-vision-token']
        if len(tokens)!=1 or type(tokens[0])is not bytes or not _capture_secrets.compare_digest(tokens[0],self.token.encode()):
            raise ValueError('Original authenticated process token differs')
        return scope


class _OriginalRequiredFlowRequest:
    """Exact original synchronous request carrier, never serialized as authority."""
    def __init__(self,selector,request):
        self.selector,self.request,self.scope=selector,request,request.scope
        self.headers=tuple(request.scope['headers']);self.thread=threading.get_ident();self.used=False

    def consume(self,profile_id,profile_sha,pipeline,image_sha,models):
        selector=self.selector
        if (type(selector)is not _OriginalRequiredFlowSelector or self.used
                or threading.get_ident()!=self.thread or self.request.scope is not self.scope
                or tuple(self.scope['headers'])!=self.headers):raise ValueError('Original request carrier changed or replayed')
        selector.current(self.request)
        if (profile_id!=selector.cohort['profile_id'] or profile_sha!=selector.cohort['execution_profile_sha256']
                or _graph_sha(pipeline)!=RETAINED_GRAPH_SHA or image_sha!=RETAINED_IMAGE_SHA):
            raise ValueError('Original fixed request data binding differs')
        _model_rows(models);_fixed_source_cohort(selector.source_root,selector.cohort)
        if not selector.lock.acquire(blocking=False):raise ValueError('Original fixed request already being selected')
        try:
            if selector.issued is not self:raise ValueError('Only the original issued request carrier may be consumed')
            if selector.used:raise ValueError('Original fixed request selector was consumed')
            self.used=True;selector.used=True
            if getattr(selector,'http_capture',None)is not None:
                from backend.engine.required_flow_http_capture import observe_original_flow_http_selection
                observe_original_flow_http_selection(self)
            # Preserve the exact cohort already validated for this issued request.
            return RequiredFlowCaptureGrant(selector.nonce,profile_id,profile_sha),selector.cohort
        finally:selector.lock.release()


def bind_original_required_flow_selector(app,current_admission,auth_type,source_root):
    """Called only by the original owned desktop factory before serving.

    It does not read a profile store, choose global compute, expose an endpoint,
    alter bootstrap frames or take permission from HTTP/JSON/environment data.
    """
    state=app.state
    admission=getattr(state,'owned_work_admission',None)
    if admission is None:return None
    if (not callable(current_admission) or not hasattr(current_admission,'__code__')
            or current_admission()is not admission or type(getattr(state,'api_token',None))is not str
            or not state.api_token or not hasattr(auth_type.__call__,'__code__')):
        raise ValueError('Original desktop factory owner/capability binding unavailable')
    if getattr(state,'required_retained_flow_capture',None)is not None:
        raise ValueError('Original startup selector cannot be replaced')
    try:_verify_fixed_source_files(source_root)
    except ValueError:return None  # different ordinary source cannot activate this fixed observation
    selector=_OriginalRequiredFlowSelector(app,current_admission,auth_type,source_root)
    if selector.shared_owner is not None and not selector.shared_owner.eligible:return None
    state.required_retained_flow_capture=selector
    return selector


def offer_original_required_flow_capture(selector,request,profile,*,pipeline,image_path,device,stop_node_id):
    if selector is None:return None
    if type(selector)is not _OriginalRequiredFlowSelector:raise ValueError('Original typed startup selector required')
    if selector.shared_owner is not None:
        # Shared bearer/cookie clients do not need the desktop process token.
        # A nonmatching passive data selector must leave their original run alone.
        try:selector.current(request)
        except ValueError:return None
    else:selector.current(request)
    if selector.used:return None
    if stop_node_id is not None or device not in ('cuda','cuda:0'):return None
    if (getattr(profile,'id',None)!=selector.cohort['profile_id']
            or _graph_sha(profile.model_dump())!=selector.cohort['execution_profile_sha256']
            or _graph_sha(pipeline)!=RETAINED_GRAPH_SHA):return None
    image=pathlib.Path(image_path)
    if (not image.is_absolute()or image.resolve(strict=True)!=image or not image.is_file()
            or image.stat().st_size!=198 or hashlib.sha256(image.read_bytes()).hexdigest()!=RETAINED_IMAGE_SHA):return None
    _fixed_source_cohort(selector.source_root,selector.cohort)
    if not selector.lock.acquire(blocking=False):raise ValueError('Original fixed request already being issued')
    try:
        if selector.used:return None
        if selector.issued is not None:raise ValueError('Original fixed request already has an issued carrier')
        carrier=_OriginalRequiredFlowRequest(selector,request)
        selector.issued=carrier
        return carrier
    finally:selector.lock.release()


def _original_private_json(path,limit=1024*1024):
    path=pathlib.Path(path);before=path.lstat()
    identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_uid,s.st_gid)
    if (path.resolve(strict=True)!=path or not stat.S_ISREG(before.st_mode)or before.st_nlink!=1
            or before.st_uid!=os.getuid()or stat.S_IMODE(before.st_mode)!=0o600 or not 0<before.st_size<=limit):
        raise ValueError('Original private operation record identity differs')
    raw=path.read_bytes()
    if identity(before)!=identity(path.lstat()):raise ValueError('Original private operation record changed')
    return json.loads(raw),hashlib.sha256(raw).hexdigest()


def original_worker_capture_binding(run_dir,spec):
    """Copy fixed original task/worker/session facts; no ownership authority."""
    root=pathlib.Path(run_dir)
    if root.resolve(strict=True)!=root or root.parent.name!='runs'or not root.name.startswith('op_'):
        raise ValueError('Original worker operation path differs')
    original,spec_sha=_original_private_json(root/'spec.json',4*1024*1024)
    status,_=_original_private_json(root/'status.json')
    worker,worker_sha=_original_private_json(root/'worker_identity.json')
    if (original!=spec or spec.get('operation')!='flowchart_run'or spec.get('task')!='rotation'
            or not _hex(spec.get('input_manifest_sha256'),64)
            or status.get('job_id')!=spec.get('job_id')or status.get('operation')!='flowchart_run'
            or status.get('spec_sha256')!=spec_sha or status.get('status')!='running'
            or worker.get('job_id')!=spec['job_id']or worker.get('run_id')!=root.name
            or worker.get('operation')!='flowchart_run'or worker.get('spec_sha256')!=spec_sha
            or worker.get('pid')!=os.getpid()or worker.get('group')!=os.getpgrp()or worker.get('session')!=os.getsid(0)
            or type(worker.get('start_ticks'))is not str or not worker['start_ticks'].isascii()
            or not worker['start_ticks'].isdecimal()or worker['start_ticks'].startswith('0')
            or type(worker.get('token'))is not str or not worker['token']):raise ValueError('Original task/worker/session records differ')
    nonce=spec.get('required_passive_capture',{}).get('nonce')
    if not _hex(nonce,32):raise ValueError('Original operation capture nonce unavailable')
    return {'schema':'modu-vision.required-original-operation-data/v1','run_id':root.name,
            'job_id':spec['job_id'],'task':spec['task'],'input_manifest_sha256':spec['input_manifest_sha256'],
            'spec_sha256':spec_sha,'worker_identity_sha256':worker_sha,'grant_nonce':nonce,
            'pid':worker['pid'],'group':worker['group'],'session':worker['session'],'start_ticks':worker['start_ticks'],
            'runtime_authority':False,'parent_credit':False}


def read_required_capture_for_original_operation(metadata,artifacts,grant,result_file):
    """Bind data to the unchanged host's verified original result archive."""
    result_file=pathlib.Path(result_file);root=result_file.parent.parent
    if artifacts.get('outputs/flowchart_result.json')!=result_file or result_file.parent.name!='outputs':
        raise ValueError('Original downloaded result file differs')
    spec,spec_sha=_original_private_json(root/'spec.json',4*1024*1024)
    archive,archive_sha=_original_private_json(root/'operation_artifacts.json',16*1024*1024)
    result,result_sha=_original_private_json(result_file,32*1024*1024)
    binding=metadata.get('operation_binding')if type(metadata)is dict else None
    if (type(binding)is not dict or binding.get('schema')!='modu-vision.required-original-operation-data/v1'
            or binding.get('run_id')!=root.name or binding.get('grant_nonce')!=grant['nonce']
            or binding.get('spec_sha256')!=spec_sha or binding.get('job_id')!=spec.get('job_id')
            or binding.get('task')!=spec.get('task')or binding.get('input_manifest_sha256')!=spec.get('input_manifest_sha256')
            or binding.get('runtime_authority')is not False or binding.get('parent_credit')is not False
            or not _hex(binding.get('worker_identity_sha256'),64)
            or spec.get('required_passive_capture',{}).get('nonce')!=grant['nonce']
            or archive.get('schema')!='modu-vision.remote-operation-archive/v1'or archive.get('op_id')!=root.name
            or archive.get('spec_sha256')!=spec_sha or result.get('required_passive_capture')!=metadata):
        raise ValueError('Original result/spec/capture data binding differs')
    manifest=archive.get('manifest',{})
    if (manifest.get('operation')!='flowchart_run'or manifest.get('job_id')!=spec['job_id']
            or manifest.get('input_manifest_sha256')!=spec['input_manifest_sha256']
            or {'path':'outputs/flowchart_result.json','size':result_file.stat().st_size,'sha256':result_sha}not in manifest.get('artifacts',[])):
        raise ValueError('Original verified result receipt differs')
    value=read_downloaded_capture_record(metadata,artifacts,grant)
    if 'downloaded_manifest'in value:
        captured,_=_original_private_json(pathlib.Path(value['downloaded_manifest']))
        provenance=captured.get('provenance',{})
        if (provenance.get('grant_nonce')!=grant['nonce']or provenance.get('operation_binding')!=binding
                or provenance.get('pipeline_sha256')!=RETAINED_GRAPH_SHA
                or provenance.get('image_sha256')!=RETAINED_IMAGE_SHA):raise ValueError('Original captured producer provenance differs')
    return {**value,'original_operation_id':root.name,'original_result_sha256':result_sha,
            'original_operation_archive_sha256':archive_sha,'runtime_authority':False,'parent_credit':False}
