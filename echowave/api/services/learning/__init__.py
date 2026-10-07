"""Learning Guide data (launch stream `learning`).

* ``core``      -- goals, lessons, evaluated practice, progress, reviews,
                   suggestions, export and deletion. Every call names the
                   organisation and the person.
* ``teacher``   -- who writes lessons and marks answers (the model, or the
                   offline sample teacher); "needs setup" without a key.
* ``sensitive`` -- whether a detail should be asked about before saving.
* ``guide``     -- the read-only interface the `agents` stream's Learning
                   Guide configuration uses.

See ``LEARNING.md`` at the repository's ``echowave/`` root.
"""
