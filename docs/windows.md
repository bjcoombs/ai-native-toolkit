# Windows setup

How to run the toolkit on Windows: the skills (`/assess`, `/fleet`, `/ghsync`,
`/ghreport` and the rest) and the test suites, without `make`.

## The baseline

- Windows 10 or 11.
- [Git for Windows](https://gitforwindows.org/), which includes Git Bash.
  Claude Code on Windows already needs it.
- Python 3.11 or later (3.12 or later for the two SVG scripts of `/assess`).
- [uv](https://docs.astral.sh/uv/), optional for running the skills, needed
  for the developer commands below (they use the locked environments).

Bash scripts and the `bash` blocks in each `SKILL.md` run under Git Bash. There
is no PowerShell rewrite of them. The Python scripts run from PowerShell or Git
Bash alike. No admin rights are needed for any of the steps below.

## Install

Each tool has a `winget` and a `choco` form; use whichever your machine allows.
`winget install --scope user` needs no admin rights. `choco` needs an elevated
shell.

| Tool | winget | choco |
|------|--------|-------|
| Git for Windows (Git Bash) | `winget install --scope user Git.Git` | `choco install git` |
| Python 3.12 | `winget install --scope user Python.Python.3.12` | `choco install python312` |
| uv (optional) | `winget install --scope user astral-sh.uv` | `choco install uv` |
| ripgrep (`rg`) | `winget install --scope user BurntSushi.ripgrep.MSVC` | `choco install ripgrep` |
| jq | `winget install --scope user jqlang.jq` | `choco install jq` |
| GitHub CLI (`gh`) | `winget install --scope user GitHub.cli` | `choco install gh` |
| GitLab CLI (`glab`) | `winget install --scope user GLab.GLab` | `choco install glab` |

What each one is for:

- **ripgrep** feeds the `/assess` and `/fleet` lint-suppression and
  promissory-marker scans. Without it those blocks read `no_rg`; the rest of
  the run still works.
- **jq** is needed by `/ghsync` and `/ghreport`. Git Bash does not include it.
- **gh** is needed by `/ghsync`, `/ghreport` and the workflow skills (`/tm`,
  `/issues`, `/fix-pr`, `/fix-develop`); **glab** only for GitLab hosts. Run
  `gh auth login` (or `glab auth login --hostname <host>`) once after install.

Without uv, install the Python dependencies of `/assess` and `/fleet` once into
the interpreter you run them with; the exact `pip` line is in
[Running /assess on Windows](../skills/assess/references/windows.md).

## PATH notes

- Open a new terminal after installing anything: an open PowerShell or Git
  Bash window keeps the PATH it started with.
- Git Bash inherits the Windows PATH, so a tool installed by winget or choco
  works in both shells.
- winget puts command aliases in `%LOCALAPPDATA%\Microsoft\WinGet\Links`. When
  policy stops it creating them, the tool is still under
  `%LOCALAPPDATA%\Microsoft\WinGet\Packages`. `/assess` and `/fleet` look for
  `rg.exe` there on their own, and `RG_PATH` can name the binary directly.
- The standalone uv installer puts uv in `%USERPROFILE%\.local\bin`; winget
  puts its alias in the WinGet Links folder above. Add the one you used to PATH
  if `uv --version` is not found.
- In PowerShell, use `python`, not `python3`. If `python` opens the Microsoft
  Store, turn off the `python.exe` and `python3.exe` App execution aliases in
  Settings.
- The bash-based test suites look up `bash` on PATH. Run them from a Git Bash
  window, where it is always present; from PowerShell, `bash` can resolve to the
  WSL launcher in System32 instead.

### Git Bash path conversion

Git Bash rewrites command arguments that look like POSIX paths into Windows
paths. A hand-run `git show <rev>:<path>` can then ask git for the wrong path.
Set `MSYS_NO_PATHCONV=1` for that command:

```bash
MSYS_NO_PATHCONV=1 git show origin/main:skills/assess/SKILL.md
```

The toolkit's own scripts pass `<rev>:<path>` through `subprocess` argument
lists, so they are not affected.

## Running the skills

In Claude Code, the skills run as on any other platform. For running the
scripts by hand:

- `/assess`: [Running /assess on Windows](../skills/assess/references/windows.md)
  has the PowerShell run sequence, the no-uv dependency line and the ripgrep
  lookup.
- `/fleet`: the [Windows section of the fleet skill](../skills/fleet/SKILL.md#windows)
  has the PowerShell forms for a ghsync root and for `--from-list`.
- `/ghsync` and `/ghreport`: run the scripts from Git Bash, with `jq` and `gh`
  (or `glab`) installed as above.

## The test suites without make

`make` is usually absent on Windows. Each command below is what the matching
`Makefile` target runs, written so it works from the repository root in
PowerShell or Git Bash: one command per line, with `uv --directory` in place
of `cd`.

Clone with LF line endings first. The `skills/assess` fixtures compare file
contents byte for byte, and Git for Windows defaults to `core.autocrlf=true`:

```bash
git clone -c core.autocrlf=false https://github.com/bjcoombs/ai-native-toolkit.git
```

### make setup

```bash
uv --directory skills/assess sync --locked
uv --directory scripts sync --locked
rg --version
```

The last line only checks that ripgrep is on PATH; the promissory-marker tests
need it.

### make lint

```bash
uv --directory skills/assess run --locked ruff check .
uv --directory skills/assess run --locked ruff check ../fleet/scripts ../marathon/scripts
uv --directory scripts run --locked ruff check .
uv --directory skills/assess run --locked mypy
uv --directory skills/assess run --locked mypy ../fleet/scripts ../marathon/scripts
uv --directory scripts run --locked mypy
```

### make check

`make check` is the lock check, the three test suites and lint:

```bash
uv --directory skills/assess lock --check
uv --directory scripts lock --check
uv --directory skills/assess run --locked pytest -v -n auto --dist loadfile --cov=scripts --cov-report=term --cov-report=xml:coverage.xml
uv --directory skills/assess run --locked pytest -v -p no:xdist -m cpu_budget --no-cov
uv --directory scripts run --locked pytest -v -n auto --dist loadfile --cov=. --cov-report=term --cov-report=xml:coverage.xml
uv run --with pytest --with pyyaml --with "pytest-xdist>=3.6,<4" pytest -v -n auto --dist loadfile tests/
```

then the four `make lint` lines above.

`-n auto --dist loadfile` runs each suite on every core with pytest-xdist, one
test file per worker, as CI does. To debug a failure serially, replace those
flags with `-p no:xdist`. The second `skills/assess` line runs the tests marked
`cpu_budget` serially: they assert an absolute CPU-time budget, which a busy
worker on the same core would double, so they skip inside the xdist pool.

Some tests need POSIX-only features (symlinks, file modes, a `#!/bin/sh` stub)
and skip on Windows, so a coverage floor can read lower there than on Linux.
The Linux CI jobs enforce the floors; on Windows you can drop the three
`--cov` flags. Which tests skip and why is in
[Contributing: CI and merge gates](contributing/ci.md).
