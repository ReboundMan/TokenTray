"""Single source of truth for the ``tokentray`` package version.

Both the top-level (``_version.py`` in the repo root) and this package
copy must agree. The top-level ``_version`` is what the frozen TokenTray
PyInstaller bundle reads (no installed-package metadata in a frozen
executable); this copy is what ``pip``-installed consumers read via
``tokentray.__version__``.
"""

__version__ = "0.5.1"
