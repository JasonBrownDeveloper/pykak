from __future__ import annotations
from dataclasses import dataclass, field
from functools import cache
from inspect import signature
from pathlib import Path
from queue import Queue
import queue
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

from .core import CoreKakConnection, q

@dataclass(frozen=True)
class SelectionDesc:
    anchor_line: int
    anchor_column: int
    head_line: int
    head_column: int

    @staticmethod
    def parse(s: str):
        parts = map(int, re.split(r'\D', s))
        return SelectionDesc(*parts)

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

    @property
    def bufstr(self) -> str:
        [[value]] = self._conn.eval_sync_up(f'''
            eval -draft %(
                exec '%'
                {self._conn.pk_send} %val(selection)
            )
        ''')
        return value

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

@dataclass(frozen=True)
class KakConnection:
    _conn: CoreKakConnection

    @staticmethod
    def init(kak_session: str | None = None, sigint_at_kak_end: bool = True):
        if kak_session is None:
            kak_session = os.environ['kak_session']
        return KakConnection(CoreKakConnection.init(kak_session, sigint_at_kak_end=sigint_at_kak_end))

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
            @self.do(client=client, mode='async')
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

    def eval(self, *cmds: str, client: str | None=None, mode: Literal['auto', 'sync', 'async', 'sync_up'] = 'auto'):
        sync: bool = mode == 'sync'
        if mode == 'auto':
            sync = self._conn.in_serving_thread()
        if mode == 'sync_up':
            self.eval_sync_up(*cmds, client=client)
        elif sync:
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

    def do(self, client: None | str = None, mode: Literal['auto', 'sync', 'async', 'sync_up'] = 'auto'):
        def inner(f: Callable[..., Any]):
            script = self.expose(f, once=True)
            self.eval(script, client=client, mode=mode)
        return inner

    def on_key(self, f: Callable[[str], Any]):
        def f_with_key():
            return f(self.val.key)
        script = self.expose(f_with_key, once=True)
        self.eval(f'''
            on-key {q(script)}
        ''')

    def get_buffer(self, bufname: str):
        [[value]] = self.eval_sync_up(f'''
            eval -draft -buffer {q(bufname)} %(
                exec '%'
                {self.pk_send} %val(selection)
            )
        ''')
        return value

    def get(self, prefix: str, name: str):
        return Value(self.eval_sync_up(f'{self.pk_send} %{prefix}({name})')[0])

    def sh(self, cmd: str) -> str:
        return self.get('sh', cmd).as_str()

    def pwd(self):
        '''
        The server's working directory. Requires bash shell (uses printf %q).
        '''
        quoted_pwd = self.sh('printf %q "$PWD"')
        return shlex.split(quoted_pwd)[0]

    def debug(self, *parts: Any):
        self.eval(q.debug(*[str(s) for s in parts]))

    def echo(self, *parts: Any):
        self.eval(q.echo(*[str(s) for s in parts]))

    def info(self, *parts: Any):
        self.eval(q.info(*[str(s) for s in parts]))

    @property
    def val(self):
        return Val(self)

    @property
    def opt(self):
        return Opt(self)

    @property
    def reg(self):
        return Reg(self)

@dataclass(frozen=False)
class LazyConnection:
    _k: KakConnection | None = None
    def __getattr__(self, name: str):
        if name in ('_pytestfixturefunction', '__bases__', '__test__'):
            raise AttributeError()
        if not self._k:
            self._k = KakConnection.init()
        return getattr(self._k, name)

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
