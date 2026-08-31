"""A rejected request must be able to say why.

Every 422 in the settings routes was built as ``detail=exc.args[0]``. Pydantic's
ValidationError is a ValueError with an empty ``args``, so that index raised
IndexError from inside the handler and the 422 became an unhandled 500 with no
body -- which the frontend renders as its generic fallback. "Failed to save
model configuration", with nothing else on screen, was this.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ValidationError

from api.utils.validation_detail import detail_for


class _Model(BaseModel):
    count: int


def _validation_error() -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        _Model(count="not a number")
    return caught.value


class TestPydanticErrors:
    def test_it_does_not_raise_where_indexing_args_did(self):
        error = _validation_error()

        assert error.args == ()
        with pytest.raises(IndexError):
            error.args[0]

        detail = detail_for(error)  # the call that used to die on args[0]

        # A list of per-field problems, not the exception stringified: the
        # frontend renders "field: message" from this shape, and a wall of
        # pydantic's own prose from the other.
        assert isinstance(detail, list)
        assert detail

    def test_it_names_the_field_and_the_problem(self):
        detail = detail_for(_validation_error())

        assert detail[0]["loc"] == ("count",)
        assert "integer" in detail[0]["msg"]

    def test_the_rejected_value_is_not_echoed_back(self):
        """`input` can be an API key on these endpoints, and a 422 body is not
        a place to repeat one back over the wire."""
        detail = detail_for(_validation_error())

        assert isinstance(detail, list)
        assert isinstance(detail[0], dict)
        assert "input" not in detail[0]
        assert "url" not in detail[0]

    def test_it_survives_being_serialised_into_a_response(self):
        detail = detail_for(_validation_error())

        assert isinstance(detail, list)
        json.dumps(detail, default=str)


class TestOrdinaryValueErrors:
    def test_a_message_is_passed_through_unchanged(self):
        assert detail_for(ValueError("Pick a voice first.")) == "Pick a voice first."

    def test_a_status_list_is_passed_through_unchanged(self):
        """What UserConfigurationValidator raises. The settings screen renders
        one line per entry, so the structure has to survive."""
        statuses = [{"model": "tts", "message": "API key is missing"}]

        assert detail_for(ValueError(statuses)) == statuses

    def test_an_argless_value_error_still_says_something(self):
        detail = detail_for(ValueError())

        assert isinstance(detail, str)
        assert detail
