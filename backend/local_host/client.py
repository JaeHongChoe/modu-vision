"""Bounded private stdio protocol; a timed-out mutation is never resent."""
import asyncio
import json
import os
from pathlib import Path
import uuid

MAX_FRAME = 8 * 1024 * 1024


class LocalHostRemoteError(RuntimeError):
    """Private helper error retaining its exact code without parsing message text."""
    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.code = code if isinstance(code, str) and len(code) <= 128 else None


class LocalHostClient:
    def __init__(self, command, *, cwd, env=None, stderr=None):
        self.command, self.cwd, self.env = command, Path(cwd), env
        self.stderr = stderr
        self.process = None
        self.ready = None
        self.pending = {}
        self.reader = None
        self.write_lock = asyncio.Lock()
        self.ready_future = None
        self.closed = False

    async def start(self, timeout=30):
        if self.process is not None: raise RuntimeError('Helper already started')
        self.ready_future = asyncio.get_running_loop().create_future()
        self.process = await asyncio.create_subprocess_exec(*self.command, cwd=self.cwd, env=self.env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=self.stderr if self.stderr is not None else asyncio.subprocess.DEVNULL,
            limit=MAX_FRAME + 1, start_new_session=os.name == 'posix')
        self.reader = asyncio.create_task(self._read())
        try:
            await asyncio.wait_for(asyncio.shield(self.ready_future), timeout)
            if self.ready is None: raise RuntimeError('Local helper exited during startup')
        except BaseException as error:
            await self.close()
            # The reader may have completed during timeout cleanup. Retire its
            # result here so failed startup never leaves an unobserved future.
            if not self.ready_future.done(): self.ready_future.cancel()
            elif not self.ready_future.cancelled(): self.ready_future.exception()
            if isinstance(error, TimeoutError):
                raise RuntimeError('Local helper readiness timed out; check the dedicated helper.log') from None
            raise
        return self.ready

    async def _read(self):
        try:
            while True:
                line = await self.process.stdout.readline()
                if not line: raise RuntimeError('Local helper exited; operation result is unconfirmed')
                if len(line) > MAX_FRAME: raise RuntimeError('Local helper frame is too large')
                try: frame = json.loads(line)
                except (ValueError, UnicodeError): raise RuntimeError('Local helper returned an invalid frame') from None
                if not isinstance(frame, dict): raise RuntimeError('Local helper returned an invalid frame')
                if self.ready is None and frame.get('event') == 'ready':
                    if frame.get('protocol') != 1 or frame.get('platform') not in {'darwin', 'linux', 'win32'}:
                        raise RuntimeError('Local helper protocol is unsupported')
                    self.ready = frame
                    if not self.ready_future.done(): self.ready_future.set_result(frame)
                    continue
                if self.ready is None: raise RuntimeError('Local helper did not identify its protocol')
                future = self.pending.pop(frame.get('id'), None)
                if future is None or future.done(): continue  # Late/retired results never reach another request.
                if frame.get('ok') is True: future.set_result(frame.get('result'))
                elif frame.get('ok') is False:
                    error = frame.get('error', {})
                    message = error.get('message') if isinstance(error, dict) else None
                    future.set_exception(LocalHostRemoteError(message[:500] if isinstance(message, str) else 'Local operation failed',
                        code=error.get('code') if isinstance(error, dict) else None))
                else: future.set_exception(RuntimeError('Local helper returned an invalid result'))
        except (Exception, asyncio.CancelledError) as error:
            # Readiness is a live transport property, never a cached startup
            # fact. A closed/malformed stream revokes every native capability.
            self.ready = None
            cause = RuntimeError(str(error) or 'Local helper closed')
            if not self.ready_future.done(): self.ready_future.set_exception(cause)
            for future in self.pending.values():
                if not future.done(): future.set_exception(cause)
            self.pending.clear()

    async def call(self, authority, method, args, *, timeout=180):
        if self.closed or self.ready is None or self.process is None or self.process.returncode is not None:
            raise RuntimeError('Local helper is unavailable')
        identifier = uuid.uuid4().hex
        frame = {'id': identifier, **{key: authority[key] for key in ('actor_id', 'session_id', 'tab_id')},
                 'method': method, 'args': args}
        encoded = json.dumps(frame, separators=(',', ':')).encode() + b'\n'
        if len(encoded) > MAX_FRAME: raise ValueError('Local operation payload is too large')
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = future
        try:
            async with self.write_lock:
                self.process.stdin.write(encoded)
                await self.process.stdin.drain()
            return await asyncio.wait_for(future, timeout)
        finally: self.pending.pop(identifier, None)

    async def close(self):
        if self.closed: return
        self.closed = True
        if self.process is not None:
            if self.process.stdin: self.process.stdin.close()
            try: await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                if self.process.returncode is None: self.process.terminate()
                try: await asyncio.wait_for(self.process.wait(), 2)
                except TimeoutError:
                    if self.process.returncode is None: self.process.kill()
                    await self.process.wait()
        if self.reader:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
