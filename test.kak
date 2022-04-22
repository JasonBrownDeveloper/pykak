def test %{
    python %{
        keval('echo -debug -- ' + q(str(val.selections)))
        keval('info -- ' + q(str(val.selections)))
    }
}
test
def supertest %{
    eval -draft %{
        exec '%<a-s>'
        eval -itersel %{
            exec 's.<ret>'
            test
        }
    }
}

fork-python -u -c 'if 1:
    from libpykak import k, q

    @k.cmd
    def history_test(a=1, b=2, c=3, d=4):
        "test something"
        u = k.eval(k.pk_send + " %val{history}")
        k.eval("echo -- " + q(f"{a} {b} {c} {d} {u}"))

    i = 0
    @k.map(key="i", mode="user")
    def _():
        "increment i"
        global i
        i += 1
        k.eval(f"info {i}")
'

