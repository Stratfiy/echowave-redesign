"""The turn strategies decide what interrupts the agent, not the transcriber.

Several STT services broadcast an interruption themselves the moment they hear
speech. That path does not consult ``turn_start_strategy``, so on a provider
left at the library default the minimum-word setting has no effect and a cough
still cuts the agent off mid-sentence.

``should_interrupt`` defaults to True and is declared per class rather than on
``STTService``, so "we did not pass it" and "this provider cannot take it" look
identical at the call site. This file tells them apart.
"""

import ast
import re
from pathlib import Path

_FACTORY = (
    Path(__file__).resolve().parents[1] / "services" / "pipecat" / "service_factory.py"
)
_SOURCE = _FACTORY.read_text()
_PIPECAT_SERVICES = (
    Path(__file__).resolve().parents[2] / "pipecat" / "src" / "pipecat" / "services"
)


def _stt_branches() -> dict[str, str]:
    fn = next(
        n
        for n in ast.parse(_SOURCE).body
        if isinstance(n, ast.FunctionDef) and n.name == "create_stt_service"
    )
    body = ast.get_source_segment(_SOURCE, fn) or ""
    parts = re.split(
        r"(?:el)?if user_config\.stt\.provider == ServiceProviders\.(\w+)\.value:", body
    )
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _classes_declaring_the_option() -> set[str]:
    """STT classes with their own ``should_interrupt``, hence defaulting True."""
    declaring = set()
    for path in _PIPECAT_SERVICES.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        text = path.read_text()
        if "should_interrupt: bool = True" not in text:
            continue
        declaring.update(re.findall(r"^class (\w*STTService)\b", text, re.MULTILINE))
    return declaring


def test_the_pipecat_source_is_where_we_think_it_is():
    """Every assertion below is vacuous if this path stops resolving."""
    assert _PIPECAT_SERVICES.is_dir(), _PIPECAT_SERVICES
    assert _classes_declaring_the_option(), "no STT class declares should_interrupt"


def test_no_stt_is_left_owning_its_own_interruption():
    declaring = _classes_declaring_the_option()
    owning: dict[str, str] = {}

    for provider, branch in _stt_branches().items():
        built = re.findall(r"(\w*STTService)\(", branch)
        if not built:
            continue
        cls = built[0]
        if cls in declaring and "should_interrupt" not in branch:
            owning[provider] = cls

    assert not owning, (
        f"These transcribers interrupt on their own: {owning}. Pass "
        "should_interrupt=False so the user turn strategies decide instead -- "
        "otherwise turn_start_strategy is bypassed for this provider and a "
        "cough cuts the agent off whatever the minimum-word setting says."
    )


def test_where_it_is_passed_it_is_always_false():
    """True would be the same bug written deliberately."""
    wrong = [
        provider
        for provider, branch in _stt_branches().items()
        if re.search(r"should_interrupt\s*=\s*True", branch)
    ]

    assert not wrong, sorted(wrong)


def test_the_frame_the_turn_strategies_need_is_still_broadcast():
    """Turning the interrupt off must not cost the speech-start signal.

    Each of these broadcasts ``UserStartedSpeakingFrame`` before consulting
    ``_should_interrupt``, so the strategies still see the user start talking
    and only the pre-emptive interruption stops.
    """
    for name in ("assemblyai", "gladia", "speechmatics"):
        text = (_PIPECAT_SERVICES / name / "stt.py").read_text()

        assert text.index("broadcast_frame(UserStartedSpeakingFrame)") < text.index(
            "if self._should_interrupt"
        ), name
