"""Shared fixtures for the tests/ suite."""
import shutil
from pathlib import Path

import pytest

# A `jq` wrapper that gives the real jq the jq 1.6 answer to `-e` over empty
# input: jq 1.6 exits 0 when there is no input value at all (no program run),
# jq 1.7 exits 4. Every other call goes to the real jq unchanged, so a test
# that puts this first on PATH sees jq 1.6's empty-input behaviour whatever
# jq is installed.
JQ16_SHIM = r"""#!/usr/bin/env bash
exit_flag=false
reads_input=true
for a in "$@"; do
  case "$a" in
    --exit-status) exit_flag=true ;;
    --null-input|--slurp) reads_input=false ;;
    --*) ;;
    -*)
      if [[ "$a" =~ ^-[a-zA-Z]+$ ]]; then
        [[ "$a" == *e* ]] && exit_flag=true
        [[ "$a" == *n* || "$a" == *s* ]] && reads_input=false
      fi ;;
  esac
done
if [ "$exit_flag" = true ] && [ "$reads_input" = true ]; then
  body=$(cat; echo x)
  body=${body%x}
  if [ -z "${body//[[:space:]]/}" ]; then
    exit 0
  fi
  printf '%s' "$body" | exec "$REAL_JQ" "$@"
fi
exec "$REAL_JQ" "$@"
"""


@pytest.fixture
def jq16_bin(tmp_path: Path) -> Path:
    """A directory holding a `jq` with jq 1.6's empty-input `-e` behaviour."""
    real = shutil.which("jq")
    if real is None:
        pytest.skip("requires jq on PATH")
    d = tmp_path / "jq16-bin"
    d.mkdir()
    shim = d / "jq"
    # LF endings and a forward-slash path, so Git Bash runs it on Windows (#753).
    shim.write_text(JQ16_SHIM.replace('"$REAL_JQ"', f'"{Path(real).as_posix()}"'), newline="\n")
    shim.chmod(0o755)
    return d
