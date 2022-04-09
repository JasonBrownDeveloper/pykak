from __future__ import annotations
from pathlib import Path
from typing import Callable, Any, Union, ClassVar
from inspect import signature
from threading import Thread
from inspect import signature
import shlex
import functools
import itertools
import kak_socket
import os
import queue
import re
import select
import shutil
import signal
import sys
import textwrap
import threading
import time
import traceback
import tempfile

def unquote(s: str) -> list[str]:
    return shlex.split(s)

def quote(v: Union[str, list[str]]) -> str:
    def quote_impl(s: str) -> str:
        return "'%s'" % s.replace("'", "''")
    if isinstance(v, str):
        return quote_impl(v)
    else:
        return ' '.join(quote_impl(s) for s in v)

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

from dataclasses import dataclass, field
from typing import Union
from itertools import cycle

from kak_socket import KakSocket
from typing import Any, cast

class _KakConnectionInstance:
    value: Union[KakConnection, None] = None

@dataclass
class KakConnection:
    kak_socket: KakSocket
    kak2py_a: Path
    kak2py_b: Path
    py2kak: Path
    pk_dir: Path

    pk_count: int
    kak_pid: int

    callbacks: dict[str, Callable[..., Any]] = field(default_factory=dict)
    _next_unique: int = 0

    def unique(self):
        self._next_unique += 1
        return self._next_unique - 1

    serving_thread: Thread = cast(Any, None)

    heartbeat_received = False

    pk_write:         str = 'uninitialized'
    pk_done:          str = 'uninitialized'
    pk_read_inf:      str = 'uninitialized'
    pk_send:          str = 'uninitialized'

    def __post_init__(self):
        for k, v in self.__dict__.items():
            if k.startswith('pk_') and v == 'uninitialized':
                setattr(self, k, self.replace_with_pk_count(k))

    def replace_with_pk_count(self, s: str) -> str:
        return s.replace('pk_', f'pk{self.pk_count}_')

    def keval_async(self, cmd: str, client: Union[str, None]=None):
        if client:
            cmd = 'eval -client %s %s' % (client, quote(cmd))
        self.kak_socket.send(cmd)

    def keval(self, response: str) -> list[list[str]]:
        assert threading.current_thread() == self.serving_thread
        self.write(response)
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
            self.keval('\n'.join([
                'echo -markup "{Error}{\\}pykak error %s: see *debug* buffer"' % quote(str(e)),
                "echo -debug pykak error %s" % quote(str(e))
            ] + [
                "echo -debug %s" % quote(line)
                for line in exc.splitlines()
            ]))
        finally:
            self.write(f'alias global {self.pk_done} nop')

    def write(self, response: str):
        with open(self.py2kak, 'w') as f:
            f.write(response)

    def read(self) -> tuple[str, list[str]]:
        with open(self.kak2py_a, 'r') as f:
            dtype, *data = unquote(f.read())
        self.kak2py_a, self.kak2py_b = self.kak2py_b, self.kak2py_a
        return (dtype, data)


    def kak_exit_waiter(self):
        kak_pid = self.kak_pid
        if not kak_pid:
            raise ValueError('kak pid not known')
        if hasattr(os, 'pidfd_open'):
            fd = os.pidfd_open(kak_pid)
            select.select([fd], [], [])
        elif hasattr(select, 'kqueue'):
            kq = select.kqueue()                                      # type: ignore
            kq.control([select.kevent(kak_pid, select.KQ_FILTER_PROC, # type: ignore
                       select.KQ_EV_ADD, select.KQ_NOTE_EXIT)], 0)    # type: ignore
            select.select([kq.fileno()], [], [])                      # type: ignore
        else:
            HEARTBEAT_INTERVAL_SECS = 60
            while True:
                self.heartbeat_received = False
                self.keval_async(f'{self.pk_write} h')
                time.sleep(HEARTBEAT_INTERVAL_SECS)
                if not self.heartbeat_received:
                    break

        os.kill(os.getpid(), signal.SIGINT)

    def serve(self):
        self.serving_thread = threading.current_thread()
        Thread(target=self.kak_exit_waiter, daemon=True).start()

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
    def instance() -> KakConnection:
        return _KakConnectionInstance.value or KakConnection.init()

    @staticmethod
    def init(kak_session: Union[str, None]=os.environ.get('kak_session')) -> KakConnection:
        if _KakConnectionInstance.value:
            raise ValueError('already initialized')

        if not kak_session:
            raise ValueError('kak_session not known')

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

        conn = _KakConnectionInstance.value = KakConnection(
            kak_socket = kak_socket,
            kak2py_a = pk_dir / 'kak2py_a.fifo',
            kak2py_b = pk_dir / 'kak2py_b.fifo',
            py2kak = pk_dir / 'py2kak.fifo',
            pk_dir = pk_dir,
            kak_pid = kak_pid,
            pk_count = pk_count,
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

def init(kak_session: str):
    return KakConnection.init(kak_session)

def instance():
    return KakConnection.instance()

def getter(prefix: str):
    def getter_impl(name: str):
        conn = instance()
        return conn.keval(f'{conn.pk_write} d %{prefix}({name})')[0]
    return getter_impl

def getter1(prefix: str):
    def getter_impl(name: str):
        return ' '.join(getter(prefix)(name))
    return getter_impl

def keval(script: str):
    conn = instance()
    return conn.keval(script)

def keval_async(script: str, client: Union[None, str] = None):
    conn = instance()
    conn.keval_async(script, client=client)

opt = getter1('opt')
reg = getter1('reg')
val = getter1('val')
optq = getter('opt')
regq = getter('reg')
valq = getter('val')
k = keval
ka = keval_async

def pk_send():
    conn = instance()
    return conn.pk_send

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

def expose(f: Callable[..., Any], name: str='', once: bool=False):
    conn = instance()
    internal_name = f'{name or f.__name__}.{conn.unique()}'
    if once:
        def f_once(*args: Any, **kws: Any):
            del conn.callbacks[internal_name]
            f(*args, **kws)
        conn.callbacks[internal_name] = f_once
    else:
        conn.callbacks[internal_name] = f
    script = f'''
        {conn.pk_write} c {quote(internal_name)} %arg(@);
        {conn.pk_read_inf};
        unalias global {conn.pk_done}
    '''
    script = ' '.join(script.split())
    return script

def command(hidden: bool=False, name: str='', override: bool=True):
    def inner(f: Callable[..., Any]):
        conn = instance()
        exposed_name = name or f.__name__
        script = expose(f, exposed_name)
        flags: list[str] = ['-params', _min_max_params(f)]
        if override:
            flags += ['-override']
        if hidden:
            flags += ['-hidden']
        if f.__doc__:
            flags += ['-docstring', quote(f.__doc__)]
        switches = ' '.join(flags)
        conn.kak_socket.send(f'''
            def {switches} {exposed_name} {quote(script)}
        ''')
    return inner

cmd = command()

def map(key: str, mode: str='normal', scope: str='global'):
    def inner(f: Callable[..., Any]):
        conn = instance()
        name = f'pk{conn.pk_count}-map-{conn.unique()}'
        command(hidden=True, name=name)(f)
        flags: list[str] = []
        if f.__doc__:
            flags += ['-docstring', quote(f.__doc__)]
        switches = ' '.join(flags)
        conn.kak_socket.send(f'''
            map {switches} {scope} {mode} {key} ': {name}<ret>'
        ''')
    return inner

def hook(hook_name: str, filter: str='.*', scope: str='global', always: bool=False, once: bool=False, group: str=''):
    def inner(f: Callable[..., Any]):
        conn = instance()
        script = expose(f, name=hook_name, once=once)
        flags: list[str] = []
        if group:
            flags += ['-group', group]
        if once:
            flags += ['-once']
        if always:
            flags += ['-always']
        switches = ' '.join(flags)
        conn.kak_socket.send(f'''
            hook {switches} {scope} {hook_name} {quote(filter)} {quote(script)}
        ''')
    return inner

def do(client: Union[None, str] = None):
    def inner(f: Callable[..., Any]):
        script = expose(f, once=True)
        keval_async(script, client=client)
    return inner
