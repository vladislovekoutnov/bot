import os, time
from typing import Tuple

PERSONA_PATH = os.getenv("PERSONA_PATH", "temshik.txt")

_cache_text = ""
_cache_mtime = 0.0

def load_persona() -> Tuple[str, float]:
    global _cache_text, _cache_mtime
    try:
        mtime = os.path.getmtime(PERSONA_PATH)
        if mtime != _cache_mtime:
            with open(PERSONA_PATH, "r", encoding="utf-8") as f:
                _cache_text = f.read()
            _cache_mtime = mtime
    except FileNotFoundError:
        _cache_text = "Личность не найдена. Проверьте файл temshik.txt."
        _cache_mtime = time.time()
    return _cache_text, _cache_mtime