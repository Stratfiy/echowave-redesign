from __future__ import annotations

import importlib
import pkgutil

# Shared infrastructure, not an integration package. `oauth2` is the
# refresh-token grant every integration and custom tool can use; importing
# it here as though it registered a package would be harmless today and
# misleading the moment somebody looks for its router.
_INTERNAL_MODULES = {"base", "loader", "registry", "oauth2", "uhi"}
_loaded = False


def ensure_integrations_loaded() -> None:
    global _loaded
    if _loaded:
        return

    package = importlib.import_module("api.services.integrations")
    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name in _INTERNAL_MODULES:
            continue
        importlib.import_module(f"{package.__name__}.{module_info.name}")

    _loaded = True
