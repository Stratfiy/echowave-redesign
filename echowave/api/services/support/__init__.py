"""Customer support and requested actions (launch stream `support`).

Handoff 33; screens 28 (customer Help and ticket), 32 (staff inbox and
case) and 33 (support action preview and execution). See ``SUPPORT.md``.

* ``sharing`` -- exactly what a person shares with support, previewed
  before it is sent and stored as shown.
* ``tickets`` -- tickets, the customer thread, staff replies, internal
  notes, assignment and the support metrics.
* ``attachments`` -- files on a ticket, through ``services/storage``.
* ``commands`` -- the typed support commands: parameters, preview, run and
  reconciliation. Nothing else can be executed; there is no shell or SQL.
* ``actions`` -- the request -> approve (a second person) -> run ->
  reconcile -> notify lifecycle around a command.
"""

from api.services import features

HELP_FLAG = "support_help"
INBOX_FLAG = "support_inbox"
ACTIONS_FLAG = "support_actions"


def help_enabled(organization_id: int | None = None) -> bool:
    return features.is_on(HELP_FLAG, organization_id)


def inbox_enabled() -> bool:
    return features.is_on(INBOX_FLAG)


def actions_enabled() -> bool:
    return features.is_on(ACTIONS_FLAG)
