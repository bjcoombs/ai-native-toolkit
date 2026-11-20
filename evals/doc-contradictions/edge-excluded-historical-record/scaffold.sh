#!/bin/bash
# Stage this case's fixture repository into the eval workspace (the current directory).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp -R "$here/../../../skills/doc-contradictions/tests/fixtures/excluded-historical/." .
git init -q && git add -A
git -c user.email=eval@example.invalid -c user.name=eval -c commit.gpgsign=false commit -qm fixture
