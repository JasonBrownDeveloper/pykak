from __future__ import annotations
from dataclasses import dataclass, field
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

from kak_socket import KakSocket

class Quoter:
    def quote_one(self, arg: str | int):
        arg = str(arg)
        if re.match(r'^[\w-]+$', arg):
            return arg
        else:
            c = "'"
            return c + arg.replace(c, c + c) + c

    def quote_many(self, *args: str | int):
        return ' '.join(self.quote_one(v) for v in args)

    def quote_call(self, name: str, *args: str | int, **kws: str | int | bool | None):
        return self.quote_many(name, *self._flags_unquoted(**kws), *args)

    __call__ = quote_many

    def __getitem__(self, name: str):
        def inner(*args: str | int, **kws: str | int | bool | None):
            return self.quote_call(name, *args, **kws)
        return inner

    __getattr__ = __getitem__

    def eval(self, *cmds: str, **kws: str | int | bool | None):
        flags = self._flags_unquoted(**kws)
        if len(cmds) == 1 and not flags:
            return cmds[0]
        else:
            return self.quote_many('eval', *flags, '\n'.join(cmds))

    def flags(self, **kws: str | int | bool | None) -> str:
        return self.quote_many(*self._flags_unquoted(**kws))

    def _flags_unquoted(self, **kws: str | int | bool | None) -> list[str]:
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
        return xs

q = Quoter()

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

    def -hidden pk_send -params 1.. %{
        pk_write d %arg{@}
    }

    def -hidden pk_ack %{
        pk_write a
    }

    def -hidden pk_stop %{
        pk_write f
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

@dataclass(frozen=False)
class _KakConnection:
    kak_socket: KakSocket
    py2kak: Path
    pk_dir: Path

    pk_count: int
    kak_pid: int
    kak2py_a: Path
    kak2py_b: Path
    serving_thread: Thread = cast(Any, None)
    heartbeat_received: bool = False
    callbacks: dict[str, Callable[..., Any]] = field(default_factory=dict)
    unique: UniqueSupply = field(default_factory=UniqueSupply)

    @property
    def pk_done(self) -> str: return self.replace_with_pk_count('pk_done')

    @property
    def pk_write(self) -> str: return self.replace_with_pk_count('pk_write')

    @property
    def pk_read_inf(self) -> str: return self.replace_with_pk_count('pk_read_inf')

    def replace_with_pk_count(self, s: str) -> str:
        return s.replace('pk_', f'pk{self.pk_count}_')

    @property
    def pk_send(self) -> str:
        return self.replace_with_pk_count('pk_send')

    def eval_sync(self, *cmds: str, client: str | None=None) -> list[list[str]]:
        assert threading.current_thread() == self.serving_thread
        cmd = q.eval(*cmds, client=client)
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

    async_queue: Queue[str] = field(default_factory=Queue)

    def eval_async(self, cmd: str, client: str | None=None):
        cmd = q.eval(cmd, client=client)
        self.async_queue.put_nowait(cmd)

    def async_waiter(self):
        while cmd := self.async_queue.get():
            self.kak_socket.send(cmd)

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
                *[
                    f'echo -debug {q(line)}'
                    for line in exc.splitlines()
                ]
            )
        finally:
            self.write(f'alias global {self.pk_done} nop')

    def write(self, cmd: str):
        with open(self.py2kak, 'w') as f:
            f.write(cmd)

    def read(self) -> tuple[str, list[str]]:
        with open(self.kak2py_a, 'r') as f:
            dtype, *data = shlex.split(f.read())
        self.kak2py_a, self.kak2py_b = self.kak2py_b, self.kak2py_a
        return dtype, data

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

        os.kill(os.getpid(), signal.SIGINT)

    def in_serving_thread(self):
        return self.serving_thread == threading.current_thread()

    def serve(self):
        self.serving_thread = threading.current_thread()
        Thread(target=self.exit_waiter, daemon=True).start()
        Thread(target=self.async_waiter, daemon=True).start()

        try:
            while True:
                dtype, data = self.read()
                if dtype == 'c':
                    self.process_call(*data)
                elif dtype == 'f':
                    break
                elif dtype == 'h':
                    self.heartbeat_received = True
        except KeyboardInterrupt:
            pass
        finally:
            shutil.rmtree(self.pk_dir)

    @staticmethod
    def init(kak_session: str) -> _KakConnection:
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

        conn = _KakConnection(
            kak_socket  = kak_socket,
            py2kak      = pk_dir / 'py2kak.fifo',
            pk_dir      = pk_dir,
            kak_pid     = kak_pid,
            pk_count    = pk_count,
            kak2py_a = pk_dir / 'kak2py_a.fifo',
            kak2py_b = pk_dir / 'kak2py_b.fifo',
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
        prelude = conn.replace_with_pk_count(prelude)
        conn.kak_socket.send(prelude)

        Thread(target=conn.serve, daemon=False).start()
        return conn

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
        return script

@dataclass(frozen=True)
class KakConnection:
    _conn: _KakConnection

    @staticmethod
    def init(kak_session: str=os.environ.get('kak_session', '')):
        assert kak_session, 'Environment variable kak_session not set!'
        return KakConnection(_KakConnection.init(kak_session))

    @property
    def expose(self):
        return self._conn.expose

    @property
    def pk_send(self) -> str:
        return self._conn.pk_send

    @property
    def eval_sync(self):
        return self._conn.eval_sync

    @property
    def eval_async(self):
        return self._conn.eval_async

    @property
    def unique(self):
        return self._conn.unique

    def eval(self, *cmds: str, client: str | None=None, mode: Literal['auto', 'sync', 'async'] = 'auto'):
        sync: bool = mode == 'sync'
        if mode == 'auto':
            sync = self._conn.in_serving_thread()
        if sync:
            self.eval_sync(*cmds, client=client)
        else:
            self.eval_async(*cmds, client=client)

    def command(self, hidden: bool=False, name: str='', override: bool=True):
        def inner(f: Callable[..., Any]):
            exposed_name = name or f.__name__
            script = self.expose(f, exposed_name)
            flags = q.flags(
                params = _min_max_params(f),
                override = override,
                hidden = hidden,
                docstring = f.__doc__
            )
            self.eval(f'''
                define-command {flags} {q(exposed_name)} {q(script)}
            ''')
            def call(*args: Any, client: str | None=None, mode: Literal['auto', 'sync', 'async'] = 'auto'):
                script = q(exposed_name, *[str(w) for w in args])
                self.eval(script, client=client, mode=mode)
            return call
        return inner

    @property
    def cmd(self): return self.command()

    def map(self, key: str, mode: str='normal', scope: str='global'):
        def inner(f: Callable[..., Any]):
            name = f'pk{self._conn.pk_count}-map-{self.unique()}'
            self.command(hidden=True, name=name)(f)
            flags = q.flags(
                docstring = f.__doc__
            )
            self.eval(f'''
                map {flags} {scope} {mode} {q(key)} ': {name}<ret>'
            ''')
        return inner

    def hook(self, hook_name: str, filter: str='.*', scope: str='global', always: bool=False, once: bool=False, group: str=''):
        def inner(f: Callable[..., Any]):
            script = self.expose(f, name=hook_name, once=once)
            flags = q.flags(
                group = group,
                once = once,
                always = always,
            )
            self.eval(f'''
                hook {flags} {scope} {hook_name} {q(filter)} {q(script)}
            ''')
        return inner

    def do(self, client: None | str = None, mode: Literal['auto', 'sync', 'async'] = 'auto'):
        def inner(f: Callable[..., Any]):
            script = self.expose(f, once=True)
            self.eval(script, client=client, mode=mode)
        return inner

    def getter(self, prefix: str, name: str):
        return self.eval_sync(f'{self._conn.pk_write} d %{prefix}({name})')[0]

    def getter1(self, prefix: str, name: str):
        return ' '.join(self.getter(prefix, name))

    def opt(self, name: str): return self.getter1('opt', name)
    def reg(self, name: str): return self.getter1('reg', name)
    def val(self, name: str): return self.getter1('val', name)

    def optq(self, name: str): return self.getter('opt', name)
    def regq(self, name: str): return self.getter('reg', name)
    def valq(self, name: str): return self.getter('val', name)

init = KakConnection.init

@dataclass(frozen=False)
class LazyConnection:
    _k: KakConnection | None = None
    def __getattr__(self, name: str):
        global k
        if not self._k:
            self._k = k = init()
        return getattr(self._k, name)

k: KakConnection = cast(Any, LazyConnection())

def _min_max_params(f: Callable[..., Any]) -> str:
    sig = list(signature(f).parameters.values())
    kw_only = [p.name for p in sig if p.kind == p.KEYWORD_ONLY]
    assert not kw_only
    params = [p for p in sig if p.kind in [p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD]]
    min_params = len([p for p in params if p.default is p.empty])
    max_params = len(params)

    if any(p.kind == p.VAR_POSITIONAL for p in sig):
        return f'{min_params}..'
    else:
        return f'{min_params}..{max_params}'
