def sh-bg -params 1 %{
    nop %sh{
        export kak_session
        (
            {
                eval "$1"
            } &
        ) >/dev/null 2>&1 </dev/null
    }
}

sh-bg %{
    python -u -c 'if 1:
        import libpykak as k
        import functools
        import textwrap

        @functools.lru_cache(maxsize=128)
        def _compile_code(code: str):
            return compile(code, "<string>", "exec")

        api = """
            opt optq
            reg regq
            val valq
            k  keval
            ka keval_async
            quote
            unquote
        """.split()

        Globals = {
            name: getattr(k, name)
            for name in api
        }

        Globals["pk_send"] = k.pk_send()

        @k.cmd
        def python(*args):
            "evaluate in python"
            args = list(args)
            code = args.pop()
            code = textwrap.dedent(code)
            exec(_compile_code(code), Globals, {"args": args})

        k.keval_async("alias global py python")
    '
}
