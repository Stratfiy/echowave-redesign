"""A quote has to include what a new agent actually does.

Every creation path builds a QA node into the agent, so review is not an upsell
somebody opts into later — it is what an agent made today runs on every call.
A quote that leaves it out is short by the whole of it on the first invoice,
which is the surprise the estimator exists to prevent, and it is short in the
one direction a price shown to a customer must never be wrong in.
"""

import inspect

from api.services.billing.addons import CALL_QA, DEFAULT_AGENT_ADDONS, KNOWLEDGE_BASE


class TestWhatANewAgentRuns:
    def test_includes_call_review(self):
        assert CALL_QA in DEFAULT_AGENT_ADDONS

    def test_leaves_out_retrieval(self):
        """Knowledge base only bills on a call where it actually fired, and an
        agent with no documents never fires it."""
        assert KNOWLEDGE_BASE not in DEFAULT_AGENT_ADDONS

    def test_is_a_real_catalogue_key(self):
        from api.services.billing.addons import catalogue

        keys = {addon.key for addon in catalogue()}
        assert DEFAULT_AGENT_ADDONS <= keys


class TestEveryQuoteUsesIt:
    """Checked by reading the source rather than by pricing.

    Pricing needs a database and a rate card; what actually went wrong here was
    four call sites and nobody passing the argument, which is visible in the
    text and stays visible when a fifth is added.
    """

    def _sources(self):
        from api.services.agent_builder import tools
        from api.services.configuration import agent_options

        return {
            "agent_options": inspect.getsource(agent_options),
            "agent_builder.tools": inspect.getsource(tools),
        }

    def test_no_call_site_quotes_without_the_defaults(self):
        for name, source in self._sources().items():
            calls = source.count("await estimate_cost_per_minute(")
            passes = source.count("addons=DEFAULT_AGENT_ADDONS")
            assert calls > 0, f"{name} no longer quotes; drop it from this test"
            assert passes == calls, (
                f"{name} makes {calls} estimates but passes the default add-ons "
                f"{passes} times — a quote missing them is short by the QA fee"
            )
