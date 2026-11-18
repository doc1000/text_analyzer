
# Console output must never break a request: on Windows, stdout can be cp1252 (a service log, a
# pipe) and the app prints non-ASCII (e.g. a check mark). Replace what cannot be encoded instead
# of raising; mid-insert, that exception caused duplicate and dropped embedding rows.
import sys as _sys

for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except Exception:  # noqa: BLE001 - not a TextIOWrapper (tests, embedded runners)
        pass
