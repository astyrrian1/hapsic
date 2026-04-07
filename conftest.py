"""Pytest conftest — makes hapsic_controller importable without __init__.py.

AppDaemon resolves `module: hapsic_controller` to the *file*
hapsic_controller.py.  If an __init__.py sits next to it, Python treats
the directory as a package and AppDaemon loads the (empty) __init__
instead → MissingAppClass crash.

This conftest uses importlib to load the .py file directly, so:
  • No __init__.py is needed anywhere under apps/
  • `import hapsic_controller` works in every test
  • The real module path stays out of sys.path (no accidental shadowing)
"""

import importlib.util
import sys
import types
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. Stub appdaemon before anything tries to import it
# ---------------------------------------------------------------------------
_appdaemon = types.ModuleType("appdaemon")
sys.modules["appdaemon"] = _appdaemon

_plugins = types.ModuleType("appdaemon.plugins")
_appdaemon.plugins = _plugins
sys.modules["appdaemon.plugins"] = _plugins

_hass = types.ModuleType("appdaemon.plugins.hass")
_plugins.hass = _hass
sys.modules["appdaemon.plugins.hass"] = _hass

_hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
_hass.hassapi = _hassapi
sys.modules["appdaemon.plugins.hass.hassapi"] = _hassapi


class _MockHass:
    """Minimal stub so HapsicController can be instantiated."""

    def __init__(self):
        self.states = {}

    def get_state(self, entity_id):
        return self.states.get(entity_id)

    def call_service(self, service, **kwargs):
        pass

    def log(self, msg, level="INFO"):
        pass

    def turn_on(self, entity_id, **kwargs):
        pass

    def turn_off(self, entity_id, **kwargs):
        pass

    def run_every(self, callback, start, interval):
        pass

    def listen_state(self, cb, entity_id):
        pass


_hassapi.Hass = _MockHass

# ---------------------------------------------------------------------------
# 2. Load hapsic_controller.py by file path (no __init__.py required)
# ---------------------------------------------------------------------------
_controller_path = Path(__file__).parent / "apps" / "hapsic-controller" / "hapsic_controller.py"
_spec = importlib.util.spec_from_file_location("hapsic_controller", _controller_path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["hapsic_controller"] = _mod
_spec.loader.exec_module(_mod)
