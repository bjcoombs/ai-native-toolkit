"""Put ``scripts/`` on ``sys.path`` for this suite in any pytest invocation.

``scripts/pyproject.toml`` sets ``pythonpath = ["."]``, but pytest reads only the
rootdir's config. When this tree is collected together with the other test trees
(``pytest skills/assess/tests tests scripts/tests`` from ``skills/assess``), that
setting does not apply, so the modules under test (``floor_anchor``,
``floor_check``, ``transform_skill`` ...) need the path added here.

``scripts/tests`` is deliberately not a package: ``skills/assess/tests`` is the
package named ``tests``, and two packages with one name cannot be collected in
one process (#1169). Test modules here import each other by bare name
(``import test_floor_pin_exempt``).
"""
import sys
from pathlib import Path

_SCRIPTS = str(Path(__file__).resolve().parent.parent)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
