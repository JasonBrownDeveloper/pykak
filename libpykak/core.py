from __future__ import annotations
from dataclasses import dataclass, field
from functools import cache
from inspect import signature
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import Any, cast, Callable, Literal
import os
import re
import select
import shlex
import shutil
import signal
import tempfile
import threading
import time
import traceback
import tracer

from .kak_socket import KakSocket

lib = '''
    decl -hidden str pk_fifo_a
    decl -hidden str pk_fifo_b

    def -hidden pk_read_1 %{
        try %{pk_read_impl;try pk_done catch pk_ack} catch %{pk_write e %val{error}}
    }

    def pk_write_a -hidden -params .. %{
        echo -to-file %opt{pk_fifo_a} -quoting shell %arg{@}
        alias global pk_write pk_write_b
    }

    def pk_write_b -hidden -params .. %{
        echo -to-file %opt{pk_fifo_b} -quoting shell %arg{@}
        alias global pk_write pk_write_a
    }

    alias global pk_write pk_write_a

    def -hidden pk_send -params .. %{
        pk_write d %arg{@}
    }

    def -hidden pk_ack %{
        pk_write a
    }

    def -hidden pk_stop %{
        pk_write f
    }

    def -hidden pk_unregister %{
        echo -debug pk_unregister start
        def -override -hidden pk_read_impl 'fail pk_unregister'
        def -override -hidden pk_write_a 'fail pk_unregister'
        def -override -hidden pk_write_b 'fail pk_unregister'
        echo -debug pk_unregister done
    }
'''

def _gen_read_cmd(cmd: str, cmds: list[str]):
    yield 'def -hidden pk_read_%s %%{' % cmd
    for i, cmd in enumerate(cmds):
        yield 'try %{' if i == 0 else '} catch %{'
        yield 'pk_read_' + cmd + ';'
        if i != len(cmds) - 1:
            yield 'pk_done'
    yield '} }'

def _gen_read_cmds():
    N = 9
    B = 4
    for i in range(1, N):
        yield ''.join(_gen_read_cmd(str(B**i), [str(B**(i - 1)) for _ in range(B)]))
    yield ''.join(_gen_read_cmd('inf', [str(B**i) for i in range(N)] + ['inf']))

lib += '\n'.join(_gen_read_cmds())

class KakException(Exception):
    pass

@dataclass(frozen=False)
class UniqueSupply:
    _next_unique: int = 0

    def __call__(self) -> int:
        self._next_unique += 1
        return self._next_unique - 1

class Quoter:
    def quote_one(self, arg: str):
        arg = str(arg)
        if re.match(r'^[\w-]+$', arg):
            return arg
        else:
            c = "'"
            return c + arg.replace(c, c + c) + c

    def quote_many(self, *args: str | list[str], **flags: str | bool | None) -> str:
        if flags and not args:
            return self.quote_many(*self.flags(**flags))
        elif flags:
            head, *tail = args
            return self.quote_many(head, *self.flags(**flags), *tail)
        else:
            return ' '.join(
                self.quote_one(v)
                for arg in args
                for v in (arg if isinstance(arg, list) else [arg])
            )

    __call__ = quote_many

    def eval(self, *cmds: str, **kws: str | bool | None):
        flags = self.flags(**kws)
        if len(cmds) == 1 and flags == ['--']:
            return cmds[0]
        else:
            return q('eval', flags, '\n'.join(cmds))

    def debug(self, *parts: str):
        return q.eval(
            *[
                q('echo', line, debug=True)
                for s in parts
                for line in s.splitlines()
            ],
        )

    def echo(self, *parts: str):
        return q('echo', ' '.join(parts))

    def info(self, *parts: str):
        return q('info', ' '.join(parts))

    def flags(self, **kws: str | bool | None) -> list[str]:
        xs: list[str] = []
        for k, v in kws.items():
            if v is True:
                xs += [f'-{k}']
            elif v is None:
                pass
            elif v is False:
                pass
            else:
                xs += [f'-{k}', str(v)]
        return xs + ['--']

q = Quoter()

@dataclass(frozen=False)
class CoreKakConnection:
    kak_socket: KakSocket
    py2kak: Path
    pk_dir: Path
    pk_count: int
    kak_pid: int
    kak2py_a: Path
    kak2py_b: Path
    sigint_at_kak_end: bool
    serving_thread: Thread | None = None
    heartbeat_received: bool = False
    callbacks: dict[str, Callable[..., Any]] = field(default_factory=dict)
    unique: UniqueSupply = field(default_factory=UniqueSupply)
    async_queue: Queue[str] = field(default_factory=Queue)
    kak2py_queue: Queue[list[str]] = field(default_factory=Queue)

    def add_pk_count(self, s: str) -> str:
        return s.replace('pk_', f'pk{self.pk_count}_')

    @property
    def pk_done(self) -> str:
        return self.add_pk_count('pk_done')

    @property
    def pk_write(self) -> str:
        return self.add_pk_count('pk_write')

    @property
    def pk_read_inf(self) -> str:
        return self.add_pk_count('pk_read_inf')

    @property
    def pk_send(self) -> str:
        return self.add_pk_count('pk_send')

    @staticmethod
    def init(kak_session: str, sigint_at_kak_end: bool = True) -> CoreKakConnection:
        kak_socket = KakSocket.init(kak_session)
        pk_dir = Path(tempfile.mkdtemp('.pykak'))
        init_fifo = pk_dir / 'kak2py_init.fifo'
        os.mkfifo(init_fifo)
        init_cmds = f'''
            try %(
                set -add global pk_counter 1
            ) catch %(
                decl -hidden int pk_counter 0
            )
            nop %sh(
                echo $PPID $kak_opt_pk_counter > {init_fifo}
            )
        '''
        kak_socket.send(init_cmds)

        with open(init_fifo, 'r') as f:
            values = f.read().split()
            kak_pid, pk_count = [int(v) for v in values]

        conn = CoreKakConnection(
            kak_socket   = kak_socket,
            py2kak       = pk_dir / 'py2kak.fifo',
            pk_dir       = pk_dir,
            kak_pid      = kak_pid,
            pk_count     = pk_count,
            kak2py_a     = pk_dir / 'kak2py_a.fifo',
            kak2py_b     = pk_dir / 'kak2py_b.fifo',
            sigint_at_kak_end = sigint_at_kak_end,
        )

        os.mkfifo(conn.kak2py_a)
        os.mkfifo(conn.kak2py_b)
        os.mkfifo(conn.py2kak)

        prelude = f'''
            {lib}
            set global pk_fifo_a {conn.kak2py_a}
            set global pk_fifo_b {conn.kak2py_b}
            def -hidden pk_read_impl %(eval %file({conn.py2kak}))
            hook -group pk{conn.pk_count}-stop global KakEnd .* pk_stop
        '''
        prelude = conn.add_pk_count(prelude)
        conn.kak_socket.send(prelude)

        def handle_sigterm(*_: Any):
            # entr sends SIGTERM on reload. unregister write and read to avoid infinite block at fifos
            conn.async_queue.put_nowait(conn.add_pk_count('pk_unregister'))
            conn.kak2py_queue.put_nowait(['f'])
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            signal.signal(signal.SIGINT, signal.SIG_DFL)

        signal.signal(signal.SIGTERM, handle_sigterm)
        signal.signal(signal.SIGINT, handle_sigterm)

        Thread(target=conn.serve, daemon=False).start()
        return conn

    def in_serving_thread(self):
        return self.serving_thread == threading.current_thread()

    def serve(self):
        self.serving_thread = threading.current_thread()
        Thread(target=self.exit_waiter, daemon=True).start()
        Thread(target=self.async_waiter, daemon=True).start()
        Thread(target=self.read_waiter, daemon=True).start()

        try:
            while True:
                dtype, data = self.read()
                if dtype == 'c':
                    self.process_call(*data)
                elif dtype == 'f':
                    print('received f, shutting down')
                    break
                elif dtype == 'h':
                    self.heartbeat_received = True
        except KeyboardInterrupt:
            print('received sigint')
            pass
        finally:
            shutil.rmtree(self.pk_dir)
            if self.sigint_at_kak_end:
                os.kill(os.getpid(), signal.SIGINT)

    def eval_sync(self, *cmds: str, client: str | None=None) -> list[list[str]]:
        assert self.in_serving_thread()
        if client:
            cmd = q.eval(*cmds, client=client)
        else:
            cmd = '\n'.join(cmds)
        self.write(cmd)
        replies: list[list[str]] = []
        while True:
            dtype, data = self.read()
            if dtype == 'a':
                return replies
            elif dtype == 'd':
                replies.append(data)
            elif dtype == 'c':
                self.process_call(*data)
            elif dtype == 'e':
                raise KakException(data[0])
            else:
                raise Exception('invalid reply type "%s"' % dtype)

    def process_call(self, internal_name: str, *args: Any):
        try:
            f = self.callbacks[internal_name]
            f(*args)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            exc = traceback.format_exc()
            self.eval_sync(
                f'''
                    echo -markup {{Error}}libpykak error {q(str(e))}: see *debug* buffer
                    echo -debug libpykak error {q(str(e))}
                ''',
                q.debug(exc)
            )
            raise e
        finally:
            self.write(f'alias global {self.pk_done} nop')

    def write(self, cmd: str):
        with open(self.py2kak, 'w') as f:
            f.write(cmd)

    def read(self) -> tuple[str, list[str]]:
        dtype, *data = self.kak2py_queue.get()
        return dtype, data

    def read_waiter(self):
        while True:
            with open(self.kak2py_a, 'r') as f:
                contents = shlex.split(f.read())
            self.kak2py_a, self.kak2py_b = self.kak2py_b, self.kak2py_a
            self.kak2py_queue.put_nowait(contents)

    def eval_async(self, *cmds: str, client: str | None=None):
        if client:
            cmd = q.eval(*cmds, client=client)
        else:
            cmd = '\n'.join(cmds)
        self.async_queue.put_nowait(cmd)

    def async_waiter(self):
        while cmd := self.async_queue.get():
            self.kak_socket.send(cmd)

    def exit_waiter(self):
        kak_pid = self.kak_pid
        if not kak_pid:
            raise ValueError('kak pid not known')
        if hasattr(os, 'pidfd_open'):
            fd = os.pidfd_open(kak_pid)  # type: ignore
            select.select([fd], [], [])  # type: ignore
        elif hasattr(select, 'kqueue'):
            kq = select.kqueue()                                      # type: ignore
            kq.control([select.kevent(kak_pid, select.KQ_FILTER_PROC, # type: ignore
                       select.KQ_EV_ADD, select.KQ_NOTE_EXIT)], 0)    # type: ignore
            select.select([kq.fileno()], [], [])                      # type: ignore
        else:
            HEARTBEAT_INTERVAL_SECS = 60
            while True:
                self.heartbeat_received = False
                self.eval_async(f'{self.pk_write} h')
                time.sleep(HEARTBEAT_INTERVAL_SECS)
                if not self.heartbeat_received:
                    break

        self.kak2py_queue.put_nowait(['f'])

    def expose(self, f: Callable[..., Any], name: str='', once: bool=False):
        internal_name = f'{name or f.__name__}.{self.unique()}'
        if once:
            def f_once(*args: Any, **kws: Any):
                del self.callbacks[internal_name]
                f(*args, **kws)
            self.callbacks[internal_name] = f_once
        else:
            self.callbacks[internal_name] = f
        script = f'''
            {self.pk_write} c {q(internal_name)} %arg(@);
            {self.pk_read_inf};
            unalias global {self.pk_done}
        '''
        script = ' '.join(script.split())
        print(f'exposed {internal_name}')
        return script

