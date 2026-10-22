"""Bundled vocabularies that let context budgets count provider tokens offline.

A provider recognizes its own model family by name and returns a text counter
built from a vocabulary in ``vocab/`` (sources and hashes in its README).
Nothing is downloaded at run time: a missing or altered file is logged and the
host falls back to its conservative one-token-per-character estimate.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
import gzip
import hashlib
import logging
from pathlib import Path
import threading

TextTokenCounter = Callable[[str], int]
VOCAB_DIR = Path(__file__).with_name("vocab")
LOGGER = logging.getLogger(__name__)


class MemoizedCounter:
    """Memoize text counts by content digest, without keeping the text alive.

    Budget checks recount the same messages for every candidate cutoff, and a
    tokenizer is far slower than hashing.
    """

    def __init__(self, count: TextTokenCounter, maxsize: int = 8192) -> None:
        self._count = count
        self._maxsize = maxsize
        self._cache: OrderedDict[bytes, int] = OrderedDict()
        self._lock = threading.Lock()

    def __call__(self, text: str) -> int:
        key = hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=16).digest()
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        value = self._count(text)
        with self._lock:
            self._cache[key] = value
            if len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)
        return value


def read_vocab(filename: str, sha256: str | None) -> bytes | None:
    """Return a bundled vocabulary file, or None unless it matches its published hash."""
    path = VOCAB_DIR / f"{filename}.gz"
    try:
        data = gzip.decompress(path.read_bytes())
    except (OSError, EOFError) as exc:
        LOGGER.warning("Vocabulary %s is unavailable (%s); counting one token per character.",
                       path.name, type(exc).__name__)
        return None
    if sha256 is None or hashlib.sha256(data).hexdigest() != sha256:
        LOGGER.error("Vocabulary %s failed its integrity check; counting one token per character.",
                     path.name)
        return None
    return data
