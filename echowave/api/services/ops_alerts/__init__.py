"""Operational alerts and the daily cost summary, for the platform's operators.

Behind the ``ops_alerts`` flag. The founder should hear about a problem
without watching a dashboard, and hear about it once:

* ``detectors`` -- call failure spikes, provider error spikes, background
  jobs not running, spend anomalies, invite requests left waiting.
* ``incidents`` -- dedupe and cooldown: one mail when something opens, one
  reminder per cooldown while it stays open, one when it resolves.
* ``summary`` -- yesterday's metered provider spend, by component, workspace
  and agent, with calls and active workspaces, each morning at 08:45 IST.
* ``signals`` -- the counters and tick stamps the detectors read that
  nothing else records.
* ``thresholds`` -- every number above, in one module.

Mail goes to ``OPS_ALERT_EMAILS`` (or every superadmin) through
``messaging.email`` as ``notifications``, scrubbed of secrets, addresses and
phone numbers on the way out (``notify``).
"""
