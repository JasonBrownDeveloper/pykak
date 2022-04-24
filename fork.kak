def fork-shell -params .. %{
    eval nop "%%sh{" "# %sh{printf '%s\n' ""$@"" | grep -Po kak_\\w+ | tr -d '\n' }" %{
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
    } "}"
}

def fork-python -params .. %{
    fork-shell python %arg{@}
}
