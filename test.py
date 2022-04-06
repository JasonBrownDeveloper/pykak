import libpykak as k
@k.cmd
def echo_test():
    k.keval("echo -debug hello world")
    k.keval("echo hello world")
