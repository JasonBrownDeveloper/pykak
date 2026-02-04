provide-module pykak %{

def fork-shell -params .. %{
    try %{
        decl -hidden str _fork_script
        decl -hidden str _fork_vars
    }
    set global _fork_script %{
        kakquote() { printf "%s" "$*" | sed "s/'/''/g; 1s/^/'/; \$s/\$/'/"; }
        (
            {
                header=">>> $1 [$$]: "
                "$@" 2>&1 | while IFS=$'\n' read line; do
                    printf '%s\n' "echo -debug -- $(kakquote "$header$line")" | kak -p "$kak_session"
                done &
            } &
        ) >/dev/null 2>&1 </dev/null
    }
    set global _fork_vars %sh{printf '%s ' "$@" | grep -Po kak_\\w+ | tr '\n' ' '}
    echo -debug "
        fork:
        # %opt{_fork_vars}
        %opt{_fork_script}
    "
    eval nop "%%sh{
        # %opt{_fork_vars}
        %opt{_fork_script}
    }"
}

def fork-python -params .. %{
    fork-shell python %arg{@}
}

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
    k.eval("trigger-user-hook pykak")
}

}

hook -once global KakBegin .* %{ require-module pykak }
