from libpykak import k
import random

i = 0

@k.cmd
def echo_test():
    global i
    i += 1
    k.keval(f"echo -debug hello world {i}")
    k.keval(f"echo hello world {i}")
    k.keval(
        "info " + k.quote(f"hello world {i}"),
        client=random.sample(k.valq('client_list'), 1)[0]
    )

@k.cmd
def echo_rec(n : str | int = '1'):
    print(n)
    n = int(n)
    if n >= 1:
        echo_rec(n-1)
        echo_rec(n-1)
    else:
        echo_test()

echo_rec(4)
