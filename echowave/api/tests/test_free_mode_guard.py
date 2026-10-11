"""While no price is shown, nothing may be charged: ``free_mode`` stays on.

You cannot charge someone you have not shown a price to, so ``free_mode``
(everything free, nobody charged, no balance floor) is only allowed to be off
once ``PRICES_SHOWN`` is true, and that needs new price copy from the founder
(AGENTS.md, "There is no checkout"). The suite itself runs with free mode off
(conftest) to exercise the money code, so this reads the product's defaults in
a fresh interpreter with the environment cleared of every switch.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ECHOWAVE = Path(__file__).resolve().parents[2]


def _product_defaults() -> dict:
    """The constants and the flag as a deployment with no overrides sees them."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"FREE_MODE_ENABLED", "FEATURE_ORG_OVERRIDES"}
    }
    code = (
        "import json\n"
        "from api import constants\n"
        "from api.services import features\n"
        "from api.services.billing import free_mode\n"
        "print(json.dumps({\n"
        "  'prices_shown': constants.PRICES_SHOWN,\n"
        "  'free_mode_constant': constants.FREE_MODE_ENABLED,\n"
        "  'free_mode_flag': free_mode.on(),\n"
        "  'free_mode_in_health': features.public()['free_mode'],\n"
        "  'checkout_open': constants.CHECKOUT_OPEN,\n"
        "}))\n"
    )
    out = (
        subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            cwd=ECHOWAVE,
            capture_output=True,
            text=True,
            check=True,
        )
        .stdout.strip()
        .splitlines()[-1]
    )
    return json.loads(out)


def test_free_mode_is_on_while_prices_are_not_shown():
    defaults = _product_defaults()
    if not defaults["prices_shown"]:
        assert defaults["free_mode_constant"] is True, (
            "FREE_MODE_ENABLED must default to on while PRICES_SHOWN is false: "
            "an account would be charged without ever being shown a price"
        )
        assert defaults["free_mode_flag"] is True
        assert defaults["free_mode_in_health"] is True


def test_the_checkout_is_closed_while_prices_are_not_shown():
    defaults = _product_defaults()
    if not defaults["prices_shown"]:
        assert defaults["checkout_open"] is False


def test_the_default_is_in_the_source_not_only_in_a_deployment():
    """A deployment can set the variable; the default it falls back to cannot
    be allowed to drift to off in code review."""
    source = (ECHOWAVE / "api" / "constants.py").read_text()
    match = re.search(
        r'^FREE_MODE_ENABLED\s*=\s*os\.getenv\(\s*"FREE_MODE_ENABLED",\s*"(\w+)"\s*\)',
        source,
        flags=re.MULTILINE,
    )
    assert match, (
        "FREE_MODE_ENABLED is no longer read from the environment with a default"
    )
    assert match.group(1).lower() == "true"


def test_the_ui_and_the_api_agree_on_whether_prices_are_shown():
    """``ui/src/lib/pricing.ts`` is the server constant's twin."""
    ui = (ECHOWAVE / "ui" / "src" / "lib" / "pricing.ts").read_text()
    ui_value = re.search(r"PRICES_SHOWN:\s*boolean\s*=\s*(true|false)", ui)
    api = (ECHOWAVE / "api" / "constants.py").read_text()
    api_value = re.search(r"^PRICES_SHOWN\s*=\s*(True|False)", api, flags=re.MULTILINE)
    assert ui_value and api_value
    assert ui_value.group(1) == api_value.group(1).lower()
