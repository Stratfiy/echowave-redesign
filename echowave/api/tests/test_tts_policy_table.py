"""Every TTS provider declares its behaviour once, with a reason.

The factory used to repeat the same three keyword arguments in sixteen
branches and classify streaming behaviour in two hand-maintained frozensets.
Both are the same failure waiting to happen: a provider added later gets
fifteen of the sixteen lines right, nothing errors, and the difference is
invisible until somebody listens to a call. That is exactly how
``audio_out_10ms_chunks`` ended up set on one telephony transport out of seven.

``TTS_POLICY`` is the single place a provider's behaviour is written down, and
these tests are what stop it becoming decorative.
"""

import ast
import re
from pathlib import Path

_FACTORY = (
    Path(__file__).resolve().parents[1] / "services" / "pipecat" / "service_factory.py"
)
_SOURCE = _FACTORY.read_text()

#: Applied for every provider by ``_create_tts_service_instance``. A branch
#: that repeats one has either drifted from the shared value or is overriding
#: it without saying why.
_SHARED_KWARGS = ("text_filters", "skip_aggregator_types", "silence_time_s")


def _branch_providers() -> set[str]:
    """Providers ``create_tts_service`` actually branches on."""
    return set(
        re.findall(
            r"user_config\.tts\.provider == ServiceProviders\.(\w+)\.value", _SOURCE
        )
    )


def _policy_rows() -> dict[str, str]:
    """Provider -> the body of its TTSPolicy(...) call."""
    table = _SOURCE[_SOURCE.index("TTS_POLICY: dict") : _SOURCE.index("#: Derived views")]
    return {
        m.group(1): m.group("body")
        for m in re.finditer(
            r"ServiceProviders\.(\w+)\.value: TTSPolicy\((?P<body>.*?)\n    \),",
            table,
            re.DOTALL,
        )
    }


def _tts_branches() -> dict[str, str]:
    fn = next(
        n
        for n in ast.parse(_SOURCE).body
        if isinstance(n, ast.FunctionDef) and n.name == "create_tts_service"
    )
    body = ast.get_source_segment(_SOURCE, fn) or ""
    parts = re.split(
        r"(?:el)?if user_config\.tts\.provider == ServiceProviders\.(\w+)\.value:", body
    )
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


class TestTheTableIsComplete:
    def test_every_provider_the_factory_builds_has_a_row(self):
        missing = _branch_providers() - set(_policy_rows())

        assert not missing, (
            f"TTS providers with no policy row: {sorted(missing)}. Add each to "
            "TTS_POLICY with a reason, having checked whether the class the "
            "factory builds holds its connection open across synthesis calls."
        )

    def test_no_row_names_a_provider_the_factory_cannot_build(self):
        """A stale row is a decision about something that no longer exists."""
        stale = set(_policy_rows()) - _branch_providers()

        assert not stale, sorted(stale)

    def test_every_row_records_why(self):
        """The reason is the point. A row without one is a value nobody can
        re-derive without reading a TTS class per provider."""
        unexplained = [
            provider
            for provider, body in _policy_rows().items()
            if not re.search(r'reason=\s*\(?\s*["\']', body)
        ]

        assert not unexplained, sorted(unexplained)

    def test_reasons_say_something(self):
        """Guards against `reason=""` satisfying the test above."""
        for provider, body in _policy_rows().items():
            text = " ".join(re.findall(r'["\']([^"\']*)["\']', body))
            assert len(text) > 20, f"{provider}: reason is too short to be one"


class TestTheSharedSettingsStayShared:
    def test_no_branch_repeats_a_shared_keyword(self):
        repeated: dict[str, list[str]] = {}

        for provider, branch in _tts_branches().items():
            found = [kwarg for kwarg in _SHARED_KWARGS if f"{kwarg}=" in branch]
            if found:
                repeated[provider] = found

        assert not repeated, (
            f"Branches passing what _create_tts_service_instance already applies: "
            f"{repeated}. Remove them, or record the difference in that "
            "provider's TTS_POLICY row so it reads as a decision."
        )

    def test_the_helper_applies_each_of_them(self):
        helper = next(
            n
            for n in ast.parse(_SOURCE).body
            if isinstance(n, ast.FunctionDef)
            and n.name == "_create_tts_service_instance"
        )
        body = ast.get_source_segment(_SOURCE, helper) or ""

        for kwarg in _SHARED_KWARGS:
            assert f'"{kwarg}"' in body, kwarg

    def test_the_helper_lets_a_branch_win(self):
        """setdefault, not assignment: a provider that genuinely differs has to
        be able to say so at its own call site."""
        helper = next(
            n
            for n in ast.parse(_SOURCE).body
            if isinstance(n, ast.FunctionDef)
            and n.name == "_create_tts_service_instance"
        )
        body = ast.get_source_segment(_SOURCE, helper) or ""

        assert "kwargs.setdefault(" in body
        assert 'kwargs["text_filters"] =' not in body


class TestTheDerivedSetsStillPartitionTheTable:
    """The two frozensets are what the older tests assert on, so they have to
    keep meaning what they meant when they were written by hand."""

    def test_a_provider_is_streaming_or_request_based_and_not_both(self):
        rows = _policy_rows()
        streaming = {p for p, b in rows.items() if "streams=True" in b}
        request = {p for p, b in rows.items() if "streams=False" in b}

        assert not (streaming & request)
        assert streaming | request == set(rows), sorted(
            set(rows) - (streaming | request)
        )

    def test_the_sets_are_derived_rather_than_maintained(self):
        """Two lists of the same providers drift; one list and a filter cannot."""
        assert "policy.streams" in _SOURCE
        assert (
            "_LOW_LATENCY_STREAMING_TTS_PROVIDERS = frozenset(\n    provider for provider"
            in _SOURCE
        )


class TestTheProvidersLeftOnPipecatsDefault:
    """Two providers never had silence_time_s set. Recorded, not changed."""

    def test_they_are_declared_rather_than_omitted(self):
        rows = _policy_rows()

        for provider in ("CAMB", "RUMIK"):
            assert "silence_time_s=None" in rows[provider], (
                f"{provider} runs on Pipecat's 2.0s trailing silence while "
                "fourteen others push 1.0s. Whatever that becomes, it has to be "
                "a value somebody chose rather than a line nobody wrote."
            )

    def test_everyone_else_gets_the_shorter_tail(self):
        rows = _policy_rows()
        on_default = {p for p, b in rows.items() if "silence_time_s=None" in b}

        assert on_default == {"CAMB", "RUMIK"}, sorted(on_default)
