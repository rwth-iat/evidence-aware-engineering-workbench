"""main.py

Entry point for the IEV4PI application.

- Imports the `main` function from the `iev4pi_transformation_tool.main` package.
- When this script is executed directly, it forwards the return value of `main()`
  to `SystemExit`, ensuring the process exits with the appropriate status code.
- This allows the package to be used both as a command‑line script and as an
  importable module.

CLI flags for testing:
  --direct-extract    Skip homepage, open directly to Extraction Review page.
"""

import sys
import iev4pi_transformation_tool.suppress_warnings  # noqa: F401 — side-effect import
from iev4pi_transformation_tool.main import main

# Standard Python entry‑point guard.
# When the file is run as a script, execute `main()` and exit with its return code.
if __name__ == "__main__":
    if "--direct-extract" in sys.argv:
        sys.argv.remove("--direct-extract")
        from iev4pi_transformation_tool.main import DIRECT_EXTRACT
        DIRECT_EXTRACT.value = True
    raise SystemExit(main())

