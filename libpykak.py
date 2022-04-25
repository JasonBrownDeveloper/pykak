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

from kak_socket import KakSocket

class Quoter:
    def quote_one(self, arg: str):
        arg = str(arg)
        if re.match(r'^[\w-]+$', arg):
            return arg
        else:
            c = "'"
            return c + arg.replace(c, c + c) + c

    def quote_many(self, *args: str | list[str], **flags: str | bool | None) -> str:
        if args and flags:
            raise ValueError('Cannot use both positional and keyword arguments')
        elif flags:
            return self.quote_many(*self.flags(**flags))
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

@dataclass(frozen=True)
class SelectionDesc:
    anchor_line: int
    anchor_column: int
    head_line: int
    head_column: int

    @staticmethod
    def parse(s: str):
        return SelectionDesc(*re.split(r'\D', s))

    def __str__(self):
        return f'{self.anchor_line}.{self.anchor_column},{self.head_line}.{self.head_column}'

    __repr__ = __str__

@dataclass(frozen=True)
class WindowRange:
    line: int
    column: int
    height: int
    width: int

class Value(list[str]):
    def as_str_list(self) -> list[str]:
        return self

    def as_str(self) -> str:
        return ' '.join(self)

    def as_int(self) -> int:
        return int(self.as_str())

    def as_int_list(self) -> list[int]:
        return [int(v) for v in self.as_str().split()]

    def as_bool(self) -> bool:
        return self.as_str() == 'true'

    def as_desc(self) -> SelectionDesc:
        return SelectionDesc.parse(self.as_str())

    def as_descs(self) -> list[SelectionDesc]:
        return [SelectionDesc.parse(v) for v in self.as_str().split()]

    def as_window_range(self) -> WindowRange:
        return WindowRange(*self.as_int_list())

    def as_type(self, type: str):
        if fn := self.parsers().get(type):
            return fn(self)
        else:
            return self
    @classmethod
    @cache
    def parsers(cls) -> dict[str, Callable[..., Any]]:
        return {
            signature(fn).return_annotation: fn
            for k, fn in cls.__dict__.items()
            if k.startswith('as_') and k != 'as_type'
        }


class Registry:
    _conn: KakConnection
    _prefix: str
    _setter_command: str | None

    def __init__(self, conn: KakConnection, prefix: str, setter_command: str | None = None):
        self._conn = conn
        self._prefix = prefix
        self._setter_command = setter_command

    def __getattr__(self, name: str) -> Value:
        type = self.__class__.__annotations__.get(name)
        value = self._conn.get(self._prefix, name)
        return value.as_type(type) # type: ignore

    def __setattr__(self, name: str, value: Any):
        if name.startswith('_'):
            return super().__setattr__(name, value)
        assert self._setter_command
        return self._conn.eval(self._setter_command + ' ' + q(name, value))

class Val(Registry):
    def __init__(self, conn: KakConnection):
        super().__init__(conn, 'val')

    buffile: str
    buf_line_count: int
    buflist: list[str]
    bufname: str
    client_list: list[str]
    client_pid: int
    client: str
    config: str
    count: int
    cursor_byte_offset: int
    cursor_char_column: int
    cursor_char_value: int
    cursor_column: int
    cursor_display_column: int
    cursor_line: int
    error: str
    history_id: int
    history: list[str]
    hook_param_capture_1: str
    hook_param_capture_2: str
    hook_param_capture_3: str
    hook_param_capture_4: str
    hook_param_capture_5: str
    hook_param_capture_6: str
    hook_param: str
    key: str
    modified: bool
    object_flags: str # pipe-sep
    register: str
    runtime: str
    selection_desc: SelectionDesc
    selection_length: int
    selections_char_desc: list[SelectionDesc]
    selections_desc: list[SelectionDesc]
    selections_display_column_desc: list[SelectionDesc]
    selections_length: list[int]
    selections: list[str]
    selection: str
    select_mode: str
    session: str
    source: str
    text: str
    timestamp: int
    uncommitted_modifications: list[str]
    user_modes: list[str]
    version: str
    window_height: int
    window_range: WindowRange
    window_width: int

class Opt(Registry):
    def __init__(self, conn: KakConnection):
        super().__init__(conn, 'opt', 'set-option global')

    aligntab:             bool
    autocomplete:         str # pipe-sep
    autoinfo:             str # pipe-sep
    autoreload:           str
    BOM:                  str
    completers:           list[str]
    debug:                str # pipe-sep
    disabled_hooks:       str # regex
    eolformat:            str
    extra_word_chars:     list[str]
    filetype:             str
    fs_check_timeout:     int
    idle_timeout:         int
    ignored_files:        str # regex
    incsearch:            bool
    indentwidth:          int
    matching_pairs:       list[str]
    modelinefmt:          str
    path:                 list[str]
    readonly:             bool
    scrolloff:            str # line,column pair
    startup_info_version: int
    static_words:         list[str]
    tabstop:              int
    ui_options:           list[str] # str-to-str-map
    writemethod:          str

class Reg(Registry):
    def __init__(self, conn: KakConnection):
        super().__init__(conn, 'reg', 'set-register')

    arobase:    list[str]
    caret:      list[str] # buffile@timestamp@main_index list[SelectionDesc]
    colon:      list[str]
    dot:        list[str]
    dquote:     list[str]
    hash:       list[int]
    percent:    list[str]
    pipe:       list[str]
    slash:      list[str]
    underscore: list[str]

    a: list[str]
    b: list[str]
    c: list[str]
    d: list[str]
    e: list[str]
    f: list[str]
    g: list[str]
    h: list[str]
    i: list[str]
    j: list[str]
    k: list[str]
    l: list[str]
    m: list[str]
    n: list[str]
    o: list[str]
    p: list[str]
    q: list[str]
    r: list[str]
    s: list[str]
    t: list[str]
    u: list[str]
    v: list[str]
    w: list[str]
    x: list[str]
    y: list[str]
    z: list[str]

@dataclass(frozen=False)
class _KakConnection:
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
    def init(kak_session: str, sigint_at_kak_end: bool = True) -> _KakConnection:
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
                    print('received f')
                    break
                elif dtype == 'h':
                    self.heartbeat_received = True
        except KeyboardInterrupt:
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

@dataclass(frozen=True)
class KakConnection:
    _conn: _KakConnection

    @staticmethod
    def init(kak_session: str | None = None, sigint_at_kak_end: bool = True):
        if kak_session is None:
            kak_session = os.environ['kak_session']
        return KakConnection(_KakConnection.init(kak_session, sigint_at_kak_end=sigint_at_kak_end))

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

    def eval_sync_up(self, *cmds: str, client: str | None=None) -> list[list[str]]:
        if self._conn.in_serving_thread():
            return self.eval_sync(*cmds, client=client)
        else:
            chan = Queue[list[list[str]] | Exception]()
            @self.do(client=client)
            def sync_up():
                try:
                    value = self.eval_sync(*cmds, client=client)
                except Exception as e:
                    value = e
                chan.put_nowait(value)
            res = chan.get()
            if isinstance(res, Exception):
                raise res
            else:
                return res

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
            flags = q(
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
    def cmd(self):
        return self.command()

    def map(self, key: str, mode: str='normal', scope: str='global'):
        def inner(f: Callable[..., Any]):
            name = f'pk{self._conn.pk_count}-map-{self.unique()}'
            self.command(hidden=True, name=name)(f)
            flags = q(
                docstring = f.__doc__
            )
            self.eval(f'''
                map {flags} {scope} {mode} {q(key)} ': {name}<ret>'
            ''')
        return inner

    def hook(self, hook_name: str, filter: str='.*', scope: str='global', always: bool=False, once: bool=False, group: str=''):
        def inner(f: Callable[..., Any]):
            script = self.expose(f, name=hook_name, once=once)
            flags = q(
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

    def on_key(self, f: Callable[[str], Any]):
        def f_with_key():
            return f(k.val.key)
        script = self.expose(f_with_key, once=True)
        self.eval(f'''
            on-key {q(script)}
        ''')

    def get(self, prefix: str, name: str):
        return Value(self.eval_sync_up(f'{self.pk_send} %{prefix}({name})')[0])

    @property
    def val(self):
        return Val(self)

    @property
    def opt(self):
        return Opt(self)

    @property
    def reg(self):
        return Reg(self)

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
    kw_only = [p.name for p in sig if p.kind == p.KEYWORD_ONLY and p.default == p.empty]
    assert not kw_only, f'Keyword-only parameters without default values: {kw_only = } ({sig = })'
    params = [p for p in sig if p.kind in [p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD]]
    min_params = len([p for p in params if p.default is p.empty])
    max_params = len(params)

    if any(p.kind == p.VAR_POSITIONAL for p in sig):
        return f'{min_params}..'
    else:
        return f'{min_params}..{max_params}'

