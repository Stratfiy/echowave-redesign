"""What a run spent on models, counted and estimated.

Two kinds of call, counted differently because they are seen differently:

* **The judge** runs here, so its calls and tokens are exact (the vendor's
  own usage numbers).
* **Decibyl** runs on the server, so this side sees only the reply and the
  model that wrote it (``payload.model``). Each turn is one model call plus
  one more for every card or chip it produced (a tool round), at an assumed
  token count per call. That is an estimate and the report says so.

Prices come from the platform's own price book
(``api/services/billing/default_rates.py``, the list prices the rate card is
seeded with), read by path so this suite needs none of the API's
dependencies. No price is written here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Assumed per Decibyl model call: the system prompt with its rules, the
#: workspace context and the thread, and a short reply. Measured on the
#: prompt as built: locally the system prompt is ~17,000 characters (~4,300
#: tokens) and the 53 tool schemas several thousand more, before the
#: workspace context and the thread.
DECIBYL_INPUT_TOKENS = 12_000
DECIBYL_OUTPUT_TOKENS = 400

#: What Auto routes a turn to when the reply does not name its model.
DEFAULT_DECIBYL_MODEL = "claude-haiku-4-5"

_RATES = Path(__file__).resolve().parents[2] / "api/services/billing/default_rates.py"


def _price_book() -> tuple[dict[str, tuple[float, float]], str | None]:
    """{model: (input, output) USD per million}, and the book's date.

    Read from the file's syntax rather than imported: importing it pulls in
    the API's settings, which want a database. Only literal rows are taken;
    a row this cannot read leaves its model unpriced, and the report lists
    unpriced models rather than counting them as free."""
    import ast

    try:
        tree = ast.parse(_RATES.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return {}, None
    as_of: str | None = None
    prices: dict[str, tuple[float, float]] = {}
    for node in tree.body:
        target = (
            node.target
            if isinstance(node, ast.AnnAssign)
            else (
                node.targets[0]
                if isinstance(node, ast.Assign) and len(node.targets) == 1
                else None
            )
        )
        name = getattr(target, "id", None)
        if name == "AS_OF" and isinstance(node.value, ast.Constant):
            as_of = str(node.value.value)
        if name != "AWS_MODEL_PRICES" or not isinstance(node.value, ast.Tuple):
            continue
        for row in node.value.elts:
            if not isinstance(row, ast.Call) or len(row.args) < 4:
                continue
            try:
                provider, model, rate_in, rate_out = (
                    ast.literal_eval(a) for a in row.args[:4]
                )
            except ValueError:
                continue
            # The Claude Platform rows: Anthropic's list, bare model ids.
            if provider == "anthropic_aws" and model:
                prices[str(model)] = (float(rate_in), float(rate_out))
    return prices, as_of


def bare_model(model: str | None) -> str:
    """``anthropic:claude-haiku-4-5`` -> ``claude-haiku-4-5``."""
    if not model:
        return DEFAULT_DECIBYL_MODEL
    return model.split(":", 1)[-1].strip() or DEFAULT_DECIBYL_MODEL


@dataclass
class Spend:
    decibyl_calls: int = 0
    decibyl_by_model: dict[str, int] = field(default_factory=dict)
    judge_calls: int = 0
    judge_input_tokens: int = 0
    judge_output_tokens: int = 0
    judge_model: str | None = None

    def add_turn(self, model: str | None, tool_rounds: int) -> None:
        calls = 1 + max(tool_rounds, 0)
        name = bare_model(model)
        self.decibyl_calls += calls
        self.decibyl_by_model[name] = self.decibyl_by_model.get(name, 0) + calls

    def add_judge(self, model: str, input_tokens: int, output_tokens: int) -> None:
        self.judge_calls += 1
        self.judge_input_tokens += input_tokens
        self.judge_output_tokens += output_tokens
        self.judge_model = model

    def merge(self, other: Spend) -> None:
        self.decibyl_calls += other.decibyl_calls
        for k, v in other.decibyl_by_model.items():
            self.decibyl_by_model[k] = self.decibyl_by_model.get(k, 0) + v
        self.judge_calls += other.judge_calls
        self.judge_input_tokens += other.judge_input_tokens
        self.judge_output_tokens += other.judge_output_tokens
        self.judge_model = self.judge_model or other.judge_model

    def summary(self) -> dict[str, Any]:
        prices, as_of = _price_book()
        unpriced: list[str] = []

        def usd(model: str, tokens_in: int, tokens_out: int) -> float:
            rate = prices.get(model)
            if rate is None:
                unpriced.append(model)
                return 0.0
            return tokens_in / 1e6 * rate[0] + tokens_out / 1e6 * rate[1]

        decibyl = sum(
            usd(m, n * DECIBYL_INPUT_TOKENS, n * DECIBYL_OUTPUT_TOKENS)
            for m, n in self.decibyl_by_model.items()
        )
        judge = (
            usd(self.judge_model, self.judge_input_tokens, self.judge_output_tokens)
            if self.judge_model
            else 0.0
        )
        return {
            "model_calls": self.decibyl_calls + self.judge_calls,
            "decibyl_calls_estimated": self.decibyl_calls,
            "decibyl_calls_by_model": dict(sorted(self.decibyl_by_model.items())),
            "judge_calls": self.judge_calls,
            "judge_tokens": {
                "input": self.judge_input_tokens,
                "output": self.judge_output_tokens,
            },
            "estimated_usd": {
                "decibyl": round(decibyl, 4),
                "judge": round(judge, 4),
                "total": round(decibyl + judge, 4),
            },
            "assumptions": {
                "decibyl_tokens_per_call": {
                    "input": DECIBYL_INPUT_TOKENS,
                    "output": DECIBYL_OUTPUT_TOKENS,
                },
                "price_book_as_of": as_of,
                "unpriced_models": sorted(set(unpriced)),
            },
        }


#: Auto sends a quick question to the everyday tier and a multi-step one to
#: the smart tier (services/routing/brain.py PRESET_FOR; the tiers are in
#: configuration/managed_tiers.py). A forecast prices the run on each.
FORECAST_MODELS = ("claude-haiku-4-5", "claude-sonnet-5-5")


def forecast(turns: int, cases: int, judged: int, judge_model: str) -> dict[str, Any]:
    """What a run of this many turns and judged cases should cost, before any
    call is made: one tool round in three turns, and a judge call of ~3,000
    tokens in and 120 out per judged case. Priced with every turn on the
    everyday model (``low``) and with every turn on the smart one (``high``);
    a real run lands between."""
    out: dict[str, Any] = {}
    for bound, model in zip(("low", "high"), FORECAST_MODELS):
        spend = Spend()
        for i in range(turns):
            spend.add_turn(model, 1 if i % 3 == 0 else 0)
        for _ in range(judged):
            spend.add_judge(judge_model, 3_000, 120)
        out[bound] = spend.summary()
    out["forecast"] = {"cases": cases, "turns": turns, "judged": judged}
    return out
