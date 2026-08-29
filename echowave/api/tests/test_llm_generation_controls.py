"""Temperature and reply length are settings, not literals in a factory branch.

Seven providers had `temperature=0.1` written into `create_llm_service_from_provider`
and no provider had a token ceiling at all. Vapi and Bolna both expose these,
and Bolna captions the token one correctly: a longer reply costs latency. On a
non-streaming turn that is not a cost knob wearing a latency label -- the caller
waits for the whole reply to generate, so the ceiling *is* the wait.

Making them configurable must not retune anything on its own, so each branch
keeps the literal it used to pass as its fallback and this test is what holds
that line. The map below is the historical record: changing a value here is
changing how existing agents talk.
"""

import ast
import re
from pathlib import Path

SERVICE_FACTORY = (
    Path(__file__).resolve().parents[1] / "services" / "pipecat" / "service_factory.py"
)
SOURCE = SERVICE_FACTORY.read_text()


def _llm_factory_source() -> str:
    """Just `create_llm_service_from_provider`.

    Scoped deliberately: `create_tts_service` branches on the same
    `ServiceProviders.OPENAI.value:` and appears earlier in the file, so a
    whole-file search reads the TTS branch and finds no temperature in it.
    """
    tree = ast.parse(SOURCE)
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name == "create_llm_service_from_provider"
    )
    return ast.get_source_segment(SOURCE, fn) or ""


LLM_FACTORY_SOURCE = _llm_factory_source()

#: provider -> the temperature its branch passed before this was configurable.
#: None means the branch passed no temperature and must still pass none.
SHIPPED_TEMPERATURE = {
    "OPENAI": 0.1,
    "GROQ": 0.1,
    "OPENROUTER": 0.1,
    "GOOGLE": 0.1,
    "GOOGLE_VERTEX": 0.1,
    "AZURE": 0.1,
    "HUGGINGFACE": 0.1,
    "MINIMAX": 1.0,
    "SARVAM": 0.5,
    "DECIBYL": None,
    "AWS_BEDROCK": None,
    "SPEACHES": None,
}


def _llm_tuning():
    """The helper, lifted out of a module that imports every vendor SDK."""
    tree = ast.parse(SOURCE)
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_llm_tuning"
    )
    ns: dict = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<extracted>", "exec"), ns)
    return ns["_llm_tuning"]


class TestNothingChangesUntilSomebodySetsIt:
    def test_an_unset_config_reproduces_the_shipped_temperature(self):
        tuning = _llm_tuning()

        for provider, shipped in SHIPPED_TEMPERATURE.items():
            result = tuning(None, None, default_temperature=shipped)
            if shipped is None:
                assert result == {}, (
                    f"{provider} passed no temperature before and must not start "
                    "passing one."
                )
            else:
                assert result == {"temperature": shipped}, provider

    def test_each_branch_still_carries_its_own_shipped_value(self):
        """A branch given the wrong fallback retunes that provider silently."""
        for provider, shipped in SHIPPED_TEMPERATURE.items():
            branch = re.search(
                rf"ServiceProviders\.{provider}\.value:(?P<body>.*?)(?=\n    elif |\n    else:)",
                LLM_FACTORY_SOURCE,
                re.DOTALL,
            )
            assert branch, f"No branch found for {provider}"
            body = branch.group("body")
            found = re.findall(r"default_temperature=([\d.]+)", body)

            if shipped is None:
                assert not found, (
                    f"{provider} passed no temperature before; it now defaults to "
                    f"{found}."
                )
            else:
                assert found and all(float(v) == shipped for v in found), (
                    f"{provider} shipped temperature={shipped}, branch says {found}"
                )

    def test_a_token_ceiling_is_omitted_unless_set(self):
        """An absent max_tokens means the provider's own default, not a number
        we invented."""
        tuning = _llm_tuning()

        assert tuning(None, None) == {}
        assert tuning(None, 250) == {"max_tokens": 250}
        assert tuning(0.3, 250) == {"temperature": 0.3, "max_tokens": 250}

    def test_a_set_value_beats_the_shipped_default(self):
        tuning = _llm_tuning()

        assert tuning(0.9, None, default_temperature=0.1) == {"temperature": 0.9}
        # 0.0 is a real choice, not "unset" -- the falsy trap this guards.
        assert tuning(0.0, None, default_temperature=0.1) == {"temperature": 0.0}


class TestTheReasoningModelsAreLeftAlone:
    def test_gpt5_is_never_sent_a_temperature(self):
        """Its branch exists because those models reject the parameter.

        Passing a configured value there would turn a slider into a failed call.
        """
        branch = re.search(
            r'if "gpt-5" in model:(?P<body>.*?)\n        return OpenAILLMService',
            LLM_FACTORY_SOURCE,
            re.DOTALL,
        )
        assert branch, "The gpt-5 branch has moved; re-check this guard."
        assert "_llm_tuning(None, max_tokens)" in branch.group("body"), (
            "The gpt-5 branch must pass max_tokens but never a temperature."
        )


class TestTheControlsReachTheCall:
    def test_they_are_carried_for_every_provider_not_just_two(self):
        assert re.search(
            r'_carry\(kwargs, user_config\.llm, "temperature", "max_tokens"\)',
            SOURCE,
        ), (
            "Both must be carried once after the provider branches, so a new "
            "provider gets them by declaring the fields and nothing else."
        )

    def test_the_declaration_lives_on_the_pipeline_classes_only(self):
        from api.services.configuration import registry as R

        for name in ("OpenAILLMService", "GroqLLMService", "SarvamLLMConfiguration"):
            fields = set(getattr(R, name).model_fields)
            assert {"temperature", "max_tokens"} <= fields, name

        # Realtime is a different path; neither control reaches it this way.
        for name in (
            "OpenAIRealtimeLLMConfiguration",
            "GoogleRealtimeLLMConfiguration",
        ):
            fields = set(getattr(R, name).model_fields)
            assert not ({"temperature", "max_tokens"} & fields), name

    def test_providers_that_chose_their_own_temperature_keep_it(self):
        from api.services.configuration import registry as R

        assert R.MiniMaxLLMConfiguration.model_fields["temperature"].default == 1.0
        assert R.SarvamLLMConfiguration.model_fields["temperature"].default == 0.5
        assert R.OpenAILLMService.model_fields["temperature"].default is None
