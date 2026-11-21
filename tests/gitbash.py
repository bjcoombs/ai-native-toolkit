"""Run the bash skill scripts the same way on Linux, macOS and Windows (#753).

On Windows the ghsync and ghreport suites run under Git Bash (the Git for
Windows baseline from #749). The helpers here keep each test's behaviour on
Linux unchanged and cover what differs on Windows:

- `BASH` is the `bash` found on PATH. A bare `"bash"` handed to
  `subprocess` on Windows is looked up in System32 before PATH, which can be
  the WSL launcher rather than Git Bash.
- `write_script` writes LF line endings. Text mode on Windows writes CRLF,
  and bash then reads `$'\\r'` at the end of every line of a stub or data file.
- `base_env` carries the variables a Windows process needs to start into an
  environment a test builds from scratch. It is empty off Windows.
- Tests join PATH entries with `os.pathsep`, which is `;` on Windows.
"""
import os
import shutil
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

BASH = shutil.which("bash") or "bash"

# Without SYSTEMROOT a Windows process cannot open sockets or load some DLLs;
# without TEMP/TMP, mktemp under Git Bash has no writable directory.
_WINDOWS_VARS = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT")


def base_env() -> dict[str, str]:
    """The variables a scratch environment needs on Windows; {} elsewhere."""
    if not IS_WINDOWS:
        return {}
    return {k: os.environ[k] for k in _WINDOWS_VARS if k in os.environ}


def write_script(path: Path, body: str) -> None:
    """Write an executable stub with LF line endings."""
    path.write_text(body, newline="\n")
    path.chmod(0o755)
