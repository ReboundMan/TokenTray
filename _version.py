"""Single source of truth for the TokenTray version string.

The PyInstaller bundle has no installed-package metadata, so reading
``importlib.metadata.version("tokentray")`` from the frozen app always
falls back. Keeping the version here lets the popup, ``pyproject.toml``
(via dynamic version), and the Inno Setup script all agree without
relying on package metadata at runtime.
"""

__version__ = "0.2.0"
