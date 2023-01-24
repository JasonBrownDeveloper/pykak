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
