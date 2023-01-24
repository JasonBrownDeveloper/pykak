from queue import Queue
from typing import *
from subprocess import Popen, DEVNULL, check_call
import random
import textwrap

from libpykak import KakConnection, q
import pytest

@pytest.fixture
def k():
    kak_session = f'temp_{random.randint(100_000, 999_999)}'
    cmd = f'kak -s {kak_session} -ui dummy -n'.split()
    with Popen(cmd, stdin=DEVNULL, stderr=DEVNULL, stdout=DEVNULL):
        k = KakConnection.init(kak_session, sigint_at_kak_end=False)
        session = k.val.session
        print(f'{session = }')
        assert session == kak_session
        yield k
        k.eval_async('kill!')
        print('waiting... ', end='', flush=True)
    # check_call(['kak', '-clear'])

def test_sync_up_do(k: KakConnection):
    queue: Queue[Any] = Queue()
    clients = k.val.client_list
    @k.do(client=clients[0], mode='sync_up')
    def _():
        k.eval(q('exec', 'ibla<esc>'))
        assert k.val.bufstr == 'bla\n'
        queue.put_nowait('do')
    queue.put_nowait('after')
    assert queue.get() == 'do'
    assert queue.get() == 'after'

def test_async_do(k: KakConnection):
    queue: Queue[Any] = Queue()
    clients = k.val.client_list
    @k.do(client=clients[0], mode='async')
    def _():
        k.eval(q('exec', 'ibla<esc>'))
        assert k.val.bufstr == 'bla\n'
        queue.put_nowait('do')
    queue.put_nowait('after')
    assert queue.get() == 'after'
    assert queue.get() == 'do'

def test_info(k: KakConnection):
    clients = k.val.client_list
    @k.do(client=clients[0], mode='sync_up')
    def _():
        k.info('bla', 'bli')
        k.debug('A debug message', 'Another', 1, {'bla': 'beep'})
    dbg = k.get_buffer('*debug*')
    assert dbg == textwrap.dedent('''
        *** This is the debug buffer, where debug info will be written ***
        A debug message
        Another
        1
        {'bla': 'beep'}

    ''').lstrip()
    assert k.val.client_list == ['client0']
