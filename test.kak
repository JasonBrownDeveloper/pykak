def test %{
    python %{
        keval('echo -debug -- ' + quote(str(valq('selections'))))
        keval('info -- ' + quote(str(valq('selections'))))
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

sh-bg %{
    python -u -c 'if 1:
        @k.cmd
        def history_test(a=1, b=2, c=3, d=4):
            "test something"
            u = k.keval(k.pk_send() + " %val{history}")
            k.keval("echo -- " + k.quote(f"{a} {b} {c} {d} {u}"))

        i = 0
        @k.map(key="i", mode="user")
        def _():
            "increment i"
            global i
            i += 1
            k.keval(f"info {i}")
    ' |& tee -a log
}
