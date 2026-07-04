"""Root conftest — guarantee the keep-names src roots are importable in every process.

pytest imports this file before collecting tests, in the controller *and* in each
xdist worker (execnet workers start Python without full site initialization, so the
editable-install `.pth` finders are not registered there). Prepending the src roots
here makes `import ontolib` / `import backend` resolve to the real `*/src` packages —
ahead of the shadowing outer `ontolib/` & `backend/` directories — under prepend mode.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).parent
for _src in ("ontolib/src", "backend/src"):
    _abs = str(_ROOT / _src)
    if _abs not in sys.path:
        sys.path.insert(0, _abs)
