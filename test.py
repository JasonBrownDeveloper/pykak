from subprocess import Popen, DEVNULL
from unittest.mock import patch
from queue import Queue
import random
import sys

def test_k():
    kak_session = f'temp_{random.randint(100_000, 999_999)}'
    cmd = f'kak -s {kak_session} -ui dummy -n'.split()
    with Popen(cmd, stdin=DEVNULL, stderr=DEVNULL, stdout=DEVNULL):
        with patch.dict('os.environ', kak_session=kak_session):
            from libpykak import k
            value = k.val.session
            print(f'{value = }')
            assert value == kak_session
            k.eval_async('kill!')
            del sys.modules['libpykak']
        print('waiting... ', end='', flush=True)
    print('done')

def test_init():
    kak_session = f'temp_{random.randint(100_000, 999_999)}'
    cmd = f'kak -s {kak_session} -ui dummy -n'.split()
    with Popen(cmd, stdin=DEVNULL, stderr=DEVNULL, stdout=DEVNULL):
        from libpykak import init
        k = init(kak_session)
        value = k.val.session
        print(f'{value = }')
        assert value == kak_session
        k.eval_async('kill!')
        del sys.modules['libpykak']
        print('waiting... ', end='', flush=True)
    print('done')


if __name__ == '__main__':
    test_k()
    test_k()
    test_init()
    test_init()
