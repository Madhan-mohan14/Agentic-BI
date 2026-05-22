import os as _os, sys as _sys

# Ensure project root is on sys.path (needed when loaded by adk eval CLI)
_project_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

# Agent Engine assigns numeric resource IDs as app_name; newer ADK versions
# reject them. Disable the validation so any string is accepted as app_name.
try:
    import google.adk.apps.app as _adk_app_mod
    _adk_app_mod.validate_app_name = lambda _name: None
except Exception:
    pass

from .agent import root_agent
from .app import app
