"""Bounded numeric expressions, not Python execution or an OS sandbox.

Only the AST forms and named NumPy operations below are interpreted. Native calls
run in-process; work/allocation limits bound them, elapsed checks are cooperative
and are not a hard timeout. This module is also copied into standalone exports.
"""
import ast
import math
import time

import numpy as np

NUMERIC_RUNTIME_VERSION = 'modu-numeric-v1'
MAX_CODE_BYTES = 8192
MAX_LINES = 32
MAX_AST_NODES = 256
MAX_DEPTH = 24
MAX_VARIABLES = 16
MAX_PIXELS = 1048576
MAX_FFT_CALLS = 4
MAX_TOTAL_ELEMENTS = 64 * 1024 * 1024
MAX_RESULT_BYTES = 512 * 1024 * 1024
MAX_WORK_BYTES = 256 * 1024 * 1024
MAX_ELAPSED_SECONDS = 5.0

_FUNCTIONS = {
    'fft2': ('image',), 'ifft2': ('image',),
    'fftshift': ('image',), 'ifftshift': ('image',),
    'notch_mask': ('image', 'fx', 'fy', 'radius'),
    'abs': ('image',), 'real': ('image',),
    'sqrt': ('image',), 'log': ('image',), 'exp': ('image',),
    'clip': ('image', 'low', 'high'), 'where': ('condition', 'yes', 'no'),
    'minimum': ('left', 'right'), 'maximum': ('left', 'right'),
}
_DEFAULTS = {'fx': .125, 'fy': 0., 'radius': .02}
_BINARY = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide}
_COMPARE = {ast.Lt: np.less, ast.LtE: np.less_equal, ast.Gt: np.greater,
            ast.GtE: np.greater_equal, ast.Eq: np.equal, ast.NotEq: np.not_equal}


def _reject(message):
    raise ValueError('Numeric code: ' + message)


def _merge(left, right):
    if left == 'scalar': return right
    if right == 'scalar' or left == right: return left
    pair = {left, right}
    if pair == {'fft_shifted', 'filter_shifted'}: return 'fft_shifted'
    if pair == {'fft_unshifted', 'filter_unshifted'}: return 'fft_unshifted'
    _reject('cannot mix spatial pixels and incompatible frequency domains')


class _Program:
    def __init__(self, code):
        if not isinstance(code, str) or not code.strip():
            _reject('requires nonempty source text')
        try:
            encoded = code.encode('utf-8')
        except UnicodeError:
            _reject('source must be valid UTF-8')
        if len(encoded) > MAX_CODE_BYTES or len(code.splitlines()) > MAX_LINES:
            _reject('source exceeds 8192 UTF-8 bytes or 32 lines')
        try:
            self.tree = ast.parse(code, mode='exec')
        except (SyntaxError, ValueError, RecursionError):
            _reject('invalid assignment/expression syntax')
        nodes = list(ast.walk(self.tree))
        if len(nodes) > MAX_AST_NODES or len(self.tree.body) > MAX_LINES:
            _reject('AST or statement budget exceeded')
        stack = [(self.tree, 0)]
        while stack:
            node, depth = stack.pop()
            if depth > MAX_DEPTH: _reject('expression depth budget exceeded')
            stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
        self.kinds = {}; self.calls = {}; self.variables = {'image': 'spatial'}
        self.fft_calls = 0
        for statement in self.tree.body:
            if (not isinstance(statement, ast.Assign) or len(statement.targets) != 1
                    or not isinstance(statement.targets[0], ast.Name)):
                _reject('only single-name assignments are supported')
            name = statement.targets[0].id
            if (not name.isascii() or name.startswith('_') or name == 'image'
                    or name in _FUNCTIONS):
                _reject('assignment name is reserved or invalid')
            kind = self._kind(statement.value)
            self.variables[name] = kind
            if len(self.variables) > MAX_VARIABLES: _reject('variable budget exceeded')
        if self.variables.get('result') != 'spatial':
            _reject('assign a spatial RGB image to result')

    def _kind(self, node):
        if isinstance(node, ast.Name):
            if node.id not in self.variables: _reject('unknown variable: ' + node.id)
            kind = self.variables[node.id]
        elif isinstance(node, ast.Constant):
            value = node.value
            if type(value) not in (int, float) or abs(value) > 1000000 or not math.isfinite(value):
                _reject('constants must be finite real numbers within +/-1000000')
            kind = 'scalar'
        elif isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub):
            kind = self._kind(node.operand)
        elif isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            kind = _merge(self._kind(node.left), self._kind(node.right))
        elif isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _COMPARE:
            kind = _merge(self._kind(node.left), self._kind(node.comparators[0]))
        elif isinstance(node, ast.Call):
            kind = self._call_kind(node)
        else:
            _reject('unsupported syntax: ' + type(node).__name__)
        self.kinds[id(node)] = kind
        return kind

    def _call_kind(self, node):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            _reject('only documented named numeric helpers are supported')
        name = node.func.id; signature = _FUNCTIONS[name]
        if len(node.args) > len(signature): _reject('too many helper arguments')
        args = dict(zip(signature, node.args))
        for keyword in node.keywords:
            if keyword.arg not in signature or keyword.arg in args:
                _reject('unknown or repeated helper argument')
            args[keyword.arg] = keyword.value
        for key in signature:
            if key not in args:
                if name == 'notch_mask' and key in _DEFAULTS:
                    args[key] = ast.Constant(value=_DEFAULTS[key])
                else: _reject('missing helper argument: ' + key)
        kinds = {key: self._kind(value) for key, value in args.items()}
        first = kinds[signature[0]]
        if name == 'fft2':
            if first != 'spatial': _reject('fft2 requires spatial pixels')
            kind = 'fft_unshifted'
        elif name == 'ifft2':
            if first != 'fft_unshifted': _reject('ifft2 requires an unshifted spectrum')
            kind = 'spatial'
        elif name == 'fftshift':
            if first != 'fft_unshifted': _reject('fftshift requires an unshifted spectrum')
            kind = 'fft_shifted'
        elif name == 'ifftshift':
            if first not in ('fft_shifted', 'filter_shifted'):
                _reject('ifftshift requires a shifted spectrum or filter')
            kind = 'fft_unshifted' if first == 'fft_shifted' else 'filter_unshifted'
        elif name == 'notch_mask':
            if first != 'spatial' or any(kinds[k] != 'scalar' for k in ('fx', 'fy', 'radius')):
                _reject('notch_mask requires spatial pixels and scalar frequencies/radius')
            kind = 'filter_shifted'
        else:
            kind = 'scalar'
            for value in kinds.values(): kind = _merge(kind, value)
        if name in ('fft2', 'ifft2'):
            self.fft_calls += 1
            if self.fft_calls > MAX_FFT_CALLS: _reject('FFT call budget exceeded')
        self.calls[id(node)] = (name, args)
        return kind


def validate_numeric_code(code):
    """Validate the entire program before any array operation is dispatched."""
    _Program(code)


class _Interpreter:
    def __init__(self, program, image):
        self.program = program
        self.started = time.monotonic(); self.elements = 0; self.bytes = 0
        self.reserve(image.shape, np.float64)
        self.values = {'image': image.astype(np.float64, copy=True)}

    def reserve(self, shape, dtype, *, work=0):
        if time.monotonic() - self.started > MAX_ELAPSED_SECONDS:
            _reject('elapsed work budget exceeded (cooperative check)')
        count = math.prod(shape); size = count * np.dtype(dtype).itemsize
        if (self.elements + count > MAX_TOTAL_ELEMENTS or self.bytes + size > MAX_RESULT_BYTES
                or size + work > MAX_WORK_BYTES):
            _reject('numeric allocation/work budget exceeded')
        self.elements += count; self.bytes += size

    def operation(self, function, values, *, dtype=None, shape=None, work=0):
        if shape is None: shape = np.broadcast_shapes(*(np.shape(value) for value in values))
        if dtype is None: dtype = np.result_type(*[np.asarray(v).dtype for v in values])
        self.reserve(shape, dtype, work=work)
        with np.errstate(over='raise', divide='raise', invalid='raise', under='ignore'):
            result = function(*values)
        if not np.all(np.isfinite(result)): _reject('nonfinite numeric result')
        if time.monotonic() - self.started > MAX_ELAPSED_SECONDS:
            _reject('elapsed work budget exceeded (cooperative check)')
        return result

    def expression(self, node):
        if isinstance(node, ast.Constant): return float(node.value)
        if isinstance(node, ast.Name): return self.values[node.id]
        if isinstance(node, ast.UnaryOp):
            value = self.expression(node.operand)
            return self.operation(np.negative if isinstance(node.op, ast.USub) else np.positive, [value])
        if isinstance(node, ast.BinOp):
            values = [self.expression(node.left), self.expression(node.right)]
            return self.operation(_BINARY[type(node.op)], values)
        if isinstance(node, ast.Compare):
            values = [self.expression(node.left), self.expression(node.comparators[0])]
            if any(np.iscomplexobj(v) for v in values): _reject('comparison requires real values')
            return self.operation(_COMPARE[type(node.ops[0])], values, dtype=np.bool_)
        name, arguments = self.program.calls[id(node)]
        args = [self.expression(arguments[key]) for key in _FUNCTIONS[name]]
        source = args[0]
        if name in ('fft2', 'ifft2'):
            size = np.size(source) * np.dtype(np.complex128).itemsize
            return self.operation(lambda a: getattr(np.fft, name)(a, axes=(0, 1)), args,
                                  dtype=np.complex128, work=size * 3)
        if name in ('fftshift', 'ifftshift'):
            return self.operation(lambda a: getattr(np.fft, name)(a, axes=(0, 1)), args)
        if name == 'notch_mask': return self.notch(*args)
        functions = {'abs': np.abs, 'real': np.real, 'sqrt': np.sqrt, 'log': np.log,
                     'exp': np.exp, 'clip': np.clip, 'where': np.where,
                     'minimum': np.minimum, 'maximum': np.maximum}
        dtype = None
        if name in ('real', 'abs'): dtype = np.float64
        if name == 'where': dtype = np.result_type(np.asarray(args[1]).dtype, np.asarray(args[2]).dtype)
        return self.operation(functions[name], args, dtype=dtype)

    def notch(self, source, fx, fy, radius):
        if any(np.ndim(v) != 0 or np.iscomplexobj(v) for v in (fx, fy, radius)):
            _reject('notch frequencies/radius must be real scalars')
        fx, fy, radius = float(fx), float(fy), float(radius)
        if not (-.5 <= fx <= .5 and -.5 <= fy <= .5 and .001 <= radius <= .25):
            _reject('notch frequencies require -0.5..0.5 and radius 0.001..0.25')
        height, width = source.shape[:2]
        shape = (height, width, 1)
        self.reserve(shape, np.float64, work=height * width * 8 * 12)
        # Account conservatively for coordinate, distance and Gaussian temporaries.
        self.reserve((height, width, 12), np.float64)
        x = np.fft.fftshift(np.fft.fftfreq(width))[None, :]
        y = np.fft.fftshift(np.fft.fftfreq(height))[:, None]
        result = np.ones((height, width), np.float64)
        for sign in (1, -1):
            dx = (x - sign * fx + .5) % 1. - .5
            dy = (y - sign * fy + .5) % 1. - .5
            result *= 1. - np.exp(-(dx * dx + dy * dy) / (2. * radius * radius))
        result[height // 2, width // 2] = 1.
        return result[:, :, None]

    def run(self, shape):
        for statement in self.program.tree.body:
            self.values[statement.targets[0].id] = self.expression(statement.value)
        result = self.values['result']
        if (np.shape(result) != shape or np.iscomplexobj(result)
                or np.asarray(result).dtype.kind not in ('f', 'i', 'u') or not np.all(np.isfinite(result))):
            _reject('result must be finite real RGB with unchanged height and width')
        self.reserve(shape, np.float64, work=math.prod(shape) * 9)
        return np.clip(np.rint(result), 0, 255).astype(np.uint8)


def apply_numeric_code(image, code):
    program = _Program(code)
    source = np.asarray(image)
    if (source.ndim != 3 or source.shape[2] != 3 or min(source.shape[:2]) < 1
            or source.dtype.kind not in ('u', 'i', 'f')):
        _reject('requires real RGB pixels')
    if source.shape[0] * source.shape[1] > MAX_PIXELS:
        _reject('input exceeds 1048576 pixels; resize/crop before this code block')
    if not np.all(np.isfinite(source)) or np.any(source < 0) or np.any(source > 255):
        _reject('input pixels must be finite in 0..255')
    try:
        return _Interpreter(program, source).run(source.shape)
    except (FloatingPointError, TypeError, OverflowError, MemoryError) as error:
        raise ValueError('Numeric code: invalid numeric operation or resource limit') from error
