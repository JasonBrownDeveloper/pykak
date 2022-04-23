from libpykak import k, q
import random

i = 0

@k.cmd
def echo_test():
    global i
    i += 1
    k.eval(
        f'echo -debug hello world {i}',
        f'echo hello world {i}',
        'info ' + q(f'hello world {i}'),
        client=random.sample(k.val.client_list, 1)[0]
    )

@k.cmd
def echo_rec(n: str | int = '1'):
    print(n)
    n = int(n)
    if n >= 1:
        echo_rec(n-1)
        echo_rec(n-1)
    else:
        echo_test()

echo_rec(4)

@k.cmd
def count_to(to: str | int):
    print(to)
    for i in range(int(to)):
        k.eval(f'echo -debug {i+1}')

count_to(10)

@k.cmd
def assign(*to: str):
    k.eval(q('try', 'decl str-list flum'))
    k.opt.flum = list(to)
    k.eval(q('try', 'decl str flum2'))
    k.opt.flum2 = str(to)
    k.reg.a = list(to)
    k.eval(q('info', repr((k.opt.flum, k.opt.flum2, k.reg.a))))

@k.cmd
def values():
    print(f'{k.val.buffile =                   }')
    print(f'{k.val.buf_line_count =            }')
    print(f'{k.val.buflist =                   }')
    print(f'{k.val.bufname =                   }')
    print(f'{k.val.client_list =               }')
    print(f'{k.val.client =                    }')
    print(f'{k.val.client_pid =                }')
    print(f'{k.val.config =                    }')
    print(f'{k.val.count =                     }')
    print(f'{k.val.cursor_byte_offset =        }')
    print(f'{k.val.cursor_char_column =        }')
    print(f'{k.val.cursor_display_column =     }')
    print(f'{k.val.cursor_char_value =         }')
    print(f'{k.val.cursor_column =             }')
    print(f'{k.val.cursor_line =               }')
    print(f'{k.val.error =                     }')
    # print(f'{k.val.history =                   }')
    print(f'{k.val.history_id =                }')
    # print(f'{k.val.hook_param =                }'1)
    # print(f'{k.val.hook_param_capture_1 =      }')
    # print(f'{k.val.hook_param_capture_2 =      }')
    # print(f'{k.val.hook_param_capture_3 =      }')
    # print(f'{k.val.hook_param_capture_4 =      }')
    # print(f'{k.val.hook_param_capture_5 =      }')
    # print(f'{k.val.hook_param_capture_6 =      }')
    print(f'{k.val.modified =                  }')
    # print(f'{k.val.object_flags =              }')
    print(f'{k.val.register =                  }')
    print(f'{k.val.runtime =                   }')
    # print(f'{k.val.select_mode =               }')
    print(f'{k.val.selection =                 }')
    print(f'{k.val.selections =                }')
    print(f'{k.val.selection_desc =            }')
    print(f'{k.val.selections_desc =           }')
    print(f'{k.val.selections_char_desc =      }')
    print(f'{k.val.selections_display_column_desc = }')
    print(f'{k.val.selection_length =          }')
    print(f'{k.val.selections_length =         }')
    print(f'{k.val.session =                   }')
    # print(f'{k.val.source =                    }')
    # print(f'{k.val.text =                      }')
    print(f'{k.val.timestamp =                 }')
    print(f'{k.val.uncommitted_modifications = }')
    print(f'{k.val.user_modes =                }')
    print(f'{k.val.version =                   }')
    print(f'{k.val.window_height =             }')
    print(f'{k.val.window_width =              }')
    print(f'{k.val.window_range =              }')

    print(f'{k.opt.tabstop =              }')
    print(f'{k.opt.indentwidth =          }')
    print(f'{k.opt.scrolloff =            }')
    print(f'{k.opt.eolformat =            }')
    print(f'{k.opt.BOM =                  }')
    print(f'{k.opt.readonly =             }')
    print(f'{k.opt.incsearch =            }')
    print(f'{k.opt.aligntab =             }')
    print(f'{k.opt.autoinfo =             }')
    print(f'{k.opt.autocomplete =         }')
    print(f'{k.opt.ignored_files =        }')
    print(f'{k.opt.disabled_hooks =       }')
    print(f'{k.opt.filetype =             }')
    print(f'{k.opt.path =                 }')
    print(f'{k.opt.completers =           }')
    print(f'{k.opt.static_words =         }')
    print(f'{k.opt.extra_word_chars =     }')
    print(f'{k.opt.matching_pairs =       }')
    print(f'{k.opt.autoreload =           }')
    print(f'{k.opt.writemethod =          }')
    print(f'{k.opt.debug =                }')
    print(f'{k.opt.idle_timeout =         }')
    print(f'{k.opt.fs_check_timeout =     }')
    print(f'{k.opt.modelinefmt =          }')
    print(f'{k.opt.ui_options =           }')
    print(f'{k.opt.startup_info_version = }')

    print(f'{k.reg.arobase =    }')
    print(f'{k.reg.caret =      }')
    print(f'{k.reg.colon =      }')
    print(f'{k.reg.dot =        }')
    print(f'{k.reg.dquote =     }')
    print(f'{k.reg.hash =       }')
    print(f'{k.reg.percent =    }')
    print(f'{k.reg.pipe =       }')
    print(f'{k.reg.slash =      }')
    print(f'{k.reg.underscore = }')
