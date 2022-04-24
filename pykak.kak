fork-python -u -c %{if 1:
    from libpykak import k, q
    import functools
    import textwrap

    @functools.lru_cache(maxsize=128)
    def _compile_code(code: str):
        return compile(code, "<string>", "exec")

    api = """
        opt
        reg
        val
        pk_send
    """.split()

    Globals = {
        name: getattr(k, name)
        for name in api
    }

    Globals["keval"] = k.eval_sync
    Globals["keval_async"] = k.eval_async
    Globals["k"] = k.eval_sync
    Globals["ka"] = k.eval_async
    Globals["q"] = q

    @k.cmd
    def python(*args):
        "evaluate in python"
        args = list(args)
        code = args.pop()
        code = textwrap.dedent(code)
        exec(_compile_code(code), Globals, {"args": args})

    k.eval("alias global py python")
}
