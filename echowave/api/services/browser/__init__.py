"""Decibyl's private browser (launch stream ``browser``, flag ``decibyl_browser``).

A person asks Decibyl to do something on real websites -- "find the
cheapest of these across these sites", "fill this form", "check my bill
on this site" -- and Decibyl opens a browser for them alone:

- ``tool.py``     the ``browse`` tool Decibyl holds
- ``session.py``  one task from the tool call to the receipt (the job)
- ``drivers.py``  where the browser runs: a box in the sandbox, locally, fake
- ``fake.py``     the scripted browser the tests run the real rules against
- ``gate.py``     every step judged before it is taken: allow, refuse, ask
- ``sites.py``    which addresses and sites may be opened (staff's list)
- ``bridge.py``   the model calls, made here with our key, never in the box
- ``cookies.py``  saved logins: cookies only, encrypted, per person
- ``channel.py``  Redis between the person's presses and the running job

The box itself is ``sandbox/browser/`` (browser-use on Chromium, behind a
proxy that refuses private addresses), started by ``sandbox/server.py``.
"""
