"""The one placeholder grammar every template kind reads.

``{{field}}`` is a field; ``{{items.col}}`` is a column of the repeating
item row. Word and Excel templates share it, so a person who has written one
kind has written the other.
"""

from __future__ import annotations

import re

PLACEHOLDER = re.compile(r"\{\{\s*([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)?)\s*\}\}")
ITEMS_PREFIX = "items."


class TemplateError(ValueError):
    """A template that cannot be read, in words a person can act on."""
