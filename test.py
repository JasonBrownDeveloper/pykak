from libpykak import k, q
import random

i = 0

@k.cmd
def echo_test():
    global i
    i += 1
    k.eval(
        f'echo -debug hello world {i}',
        f'echo hello world {i}',
        'info ' + q(f'hello world {i}'),
        client=random.sample(k.valq('client_list'), 1)[0]
    )

@k.cmd
def echo_rec(n: str | int = '1'):
    print(n)
    n = int(n)
    if n >= 1:
        echo_rec(n-1)
        echo_rec(n-1)
    else:
        echo_test()

echo_rec(4)

@k.cmd
def count_to(to: str | int):
    print(to)
    for i in range(int(to)):
        k.eval(f'echo -debug {i+1}')

count_to(10)
