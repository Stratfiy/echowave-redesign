"""The in-product chat that turns a conversation into a working agent.

Split so the expensive parts are testable without a model or a database:

* ``assemble`` — template plus answers to a valid workflow. Pure.
* ``client``   — one tool-calling loop over Anthropic, OpenAI and Gemini.
* ``limits``   — the daily cap on our own model spend.
* ``settings`` — which vendor's key is installed, and so which model runs.
* ``tools``    — what the builder may do, and what it deliberately may not.
* ``session``  — the loop that ties them together.
"""
