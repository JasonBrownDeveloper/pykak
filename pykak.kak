def bg-shell -params .. %{
    nop %sh{
        export kak_session
        (
            {
                header=">>> $1 [$$]: "
                kakquote() { printf "%s" "$*" | sed "s/'/''/g; 1s/^/'/; \$s/\$/'/"; }
                "$@" 2>&1 | while IFS=$'\n' read line; do
                    printf '%s\n' "echo -debug -- $(kakquote "$header$line")" | kak -p "$kak_session"
                done &
            } &
        ) >/dev/null 2>&1 </dev/null
    }
}

def bg-python -params .. %{
    bg-shell python %arg{@}
}

bg-python -u -c %{if 1:
    from libpykak import k
    import functools
    import textwrap

    @functools.lru_cache(maxsize=128)
    def _compile_code(code: str):
        return compile(code, "<string>", "exec")

    api = """
        opt optq
        reg regq
        val valq
        keval
        keval_async
        quote
        unquote
        pk_send
    """.split()

    Globals = {
        name: getattr(k, name)
        for name in api
    }

    Globals["k"] = k.keval
    Globals["ka"] = k.keval_async

    @k.cmd
    def python(*args):
        "evaluate in python"
        args = list(args)
        code = args.pop()
        code = textwrap.dedent(code)
        exec(_compile_code(code), Globals, {"args": args})

    k.keval_async("alias global py python")
}
