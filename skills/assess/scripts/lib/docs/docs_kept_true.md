# Docs kept true by CI (`lib.ci.docs_kept_true`, #852)

The module docstring has the rules and the block shape. Known limits:

- Only GitHub Actions workflows directly under `.github/workflows/` and `.gitlab-ci.yml` are
  read. A GitLab `include:` is not followed, and a check run only by Prow, Jenkins or another
  CI system reads not measured (`ci_not_read`) when nothing else is found.
- A Gradle or Maven verify task is not read as a check: the follower resolves the plugins and
  tasks, but no table says which of them regenerate and diff.
- `git status --porcelain` with `test -z`, and `git diff-index --quiet`, are not read as a
  diff check.
- "Regenerated" means any earlier command in the same job, script or recipe that is not a
  `git`, shell built-in or install command. A job that runs only `actions/checkout` and a
  generator action before the diff has no such command and is not credited.
- A followed script or recipe line is masked by its own `||` rule only: a Makefile `-`
  prefix (ignore errors) is dropped by the follower, so `-git diff --exit-code` counts.
- `covers` holds the literal paths a check names. A diff over the whole tree or a doctest
  run with no path names no subtree, so it meets the condition but leaves staleness alone.
  A verify and update pair names no subtree.
- The doctest flag in a pytest configuration is found by text (`--doctest-` outside a `#`
  comment) in the four root files; a configuration under a sub-folder is not read.
