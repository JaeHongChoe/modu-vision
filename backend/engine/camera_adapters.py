"""Trusted in-process camera contract and bounded simulator; no vendor loading.

SDK adapters convert buffers to uint8 BGR and bound open/read/release themselves.
Service timestamps are host read completion. No sensor-clock or hardware claim.
"""
from dataclasses import dataclass
from typing import Callable, Protocol
import numpy as np


class CameraAdapter(Protocol):
    def isOpened(self) -> bool: ...
    def read(self) -> tuple[bool, np.ndarray | None]: ...
    def release(self) -> None: ...


def validated_frame(frame):
    if (not isinstance(frame,np.ndarray) or frame.dtype!=np.uint8 or frame.ndim!=3
            or frame.shape[2]!=3 or min(frame.shape[:2])<1 or frame.nbytes>64*1024*1024):
        raise ValueError('Camera adapters must return bounded uint8 BGR frames')
    # Vendor buffers may be reused on the next read. Admission owns bytes.
    return frame.copy(order='C')


def read_frame(adapter):
    result=adapter.read()
    if not isinstance(result,tuple) or len(result)!=2 or type(result[0]) is not bool:
        raise ValueError('Camera read must return (bool, uint8 BGR frame or None)')
    return (True,validated_frame(result[1])) if result[0] else (False,None)


@dataclass(frozen=True)
class CameraAdapterFactory:
    kind: str
    open: Callable[[str | int], CameraAdapter]

    def __post_init__(self):
        if self.kind not in {'opencv','sdk','simulator'} or not callable(self.open):
            raise ValueError('Camera factory requires an explicit kind and trusted callable')

    def create(self, source) -> CameraAdapter:
        adapter=self.open(source)
        if not all(callable(getattr(adapter,name,None)) for name in ('isOpened','read','release')):
            close=getattr(adapter,'release',None)
            if callable(close): close()
            raise ValueError('Camera adapter must provide isOpened/read/release')
        return adapter


class SimulatedCamera:
    """Finite supplied frames only. Exhaustion disconnects; release is idempotent.

    No hardware, network, sensor timestamps, invisible loss or automatic replay.
    """
    def __init__(self, frames):
        if not isinstance(frames,(list,tuple)) or not 1<=len(frames)<=512:
            raise ValueError('Simulator requires 1..512 bounded uint8 BGR frames')
        self._frames=[];total=0
        for frame in frames:
            owned=validated_frame(frame);total+=owned.nbytes
            if total>256*1024*1024:raise ValueError('Simulator uint8 BGR buffers exceed 256 MiB')
            self._frames.append(owned)
        self._next=0;self.released=False

    def isOpened(self): return not self.released and self._next<len(self._frames)

    def read(self):
        if not self.isOpened():return False,None
        frame=self._frames[self._next].copy();self._next+=1
        return True,frame

    def release(self):
        self.released=True;self._frames.clear()
