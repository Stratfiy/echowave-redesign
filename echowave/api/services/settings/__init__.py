"""Launch stream `settings` (LAUNCH-PLAN.md phase 2; SETTINGS.md).

One module per screen family, each behind its own flag:

* ``profile`` -- the person's own preferences beyond controls' four, and
  the onboarding answers read into them (``settings_shell``).
* ``memory`` -- the memory manager (``memory_manager``).
* ``temporary`` -- temporary conversations and the memory pause
  (``memory_manager``).
* ``saved`` -- saved items and scoped search (``saved_items``).
* ``privacy`` -- personal export and deletion (``privacy_center``).
* ``models`` -- model defaults with inheritance (``model_inheritance``).
* ``cards`` -- the approval cards these screens raise, on the controls card
  machinery (``services/workflow/actions.py``).
"""

SETTINGS_SHELL = "settings_shell"
MEMORY_MANAGER = "memory_manager"
PRIVACY_CENTER = "privacy_center"
SAVED_ITEMS = "saved_items"
MODEL_INHERITANCE = "model_inheritance"
