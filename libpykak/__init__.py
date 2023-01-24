from typing import *
from .core import KakException, q
from .bling import KakConnection, Registry, LazyConnection

k: KakConnection = cast(Any, LazyConnection())

__all__ = [
    'KakException',
    'q',
    'KakConnection',
    'Registry',
    'k'
]
