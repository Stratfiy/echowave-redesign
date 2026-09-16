"""Which arguments the model is offered for a connected-app tool.

The defect this closes: Composio publishes every argument an action takes,
and the model was handed all of them. GMAIL_SEND_EMAIL declares around
twenty. A person saying "email Ravi the quote" produced a prompt carrying
twenty fields and a model choosing among them.

n8n's answer, in create-node-as-tool.ts, is that a node's tool schema is not
the vendor's argument list -- it is only the fields the operator marked
$fromAI(). We have nobody to do that marking, so it is done here.
"""

from api.services.integrations.composio import model_fields


def _schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required)}


def _field(type_="string", description=None):
    spec = {"type": type_}
    if description is not None:
        spec["description"] = description
    return spec


def _names(chosen):
    return [f["name"] for f in chosen]


class TestNothingUsable:
    def test_a_schema_that_is_not_a_schema_offers_nothing(self):
        assert model_fields.chosen(None) == []
        assert model_fields.chosen("gmail") == []
        assert model_fields.chosen({}) == []

    def test_a_schema_with_no_properties_offers_nothing(self):
        """The tool is then created as it was before this existed."""
        assert model_fields.chosen({"type": "object", "properties": {}}) == []


class TestRequiredFields:
    def test_every_required_field_is_offered(self):
        schema = _schema(
            {"to": _field(), "subject": _field(), "body": _field()},
            required=["to", "subject", "body"],
        )
        assert set(_names(model_fields.chosen(schema))) == {"to", "subject", "body"}
        assert all(f["required"] for f in model_fields.chosen(schema))

    def test_a_required_field_survives_the_cap(self):
        """The cap is a preference. A missing required argument is a failure.

        An action needing eight arguments gets eight, because offering six of
        them is a tool that cannot be called at all.
        """
        properties = {f"arg_{n}": _field() for n in range(8)}
        schema = _schema(properties, required=list(properties))
        chosen = model_fields.chosen(schema, limit=6)
        assert len(chosen) == 8

    def test_required_named_in_the_schema_but_absent_from_properties_is_ignored(self):
        """A vendor contradicting itself must not produce a nameless field."""
        schema = _schema({"to": _field()}, required=["to", "ghost"])
        assert _names(model_fields.chosen(schema)) == ["to"]


class TestOptionalFields:
    def test_an_optional_field_nobody_dictates_is_not_offered(self):
        schema = _schema(
            {"to": _field(), "is_html": _field("boolean"), "thread_id": _field()},
            required=["to"],
        )
        assert _names(model_fields.chosen(schema)) == ["to"]

    def test_the_words_people_say_are_offered(self):
        schema = _schema(
            {"to": _field(), "subject": _field(), "body": _field()}, required=["to"]
        )
        assert set(_names(model_fields.chosen(schema))) == {"to", "subject", "body"}

    def test_a_prefixed_name_still_matches(self):
        """`recipient_email` is `recipient`; the vendor's spelling varies."""
        schema = _schema({"recipient_email": _field(), "zzz_other": _field()})
        assert _names(model_fields.chosen(schema)) == ["recipient_email"]

    def test_optional_order_is_by_meaning_not_by_the_alphabet(self):
        """The vendor sorts alphabetically, which is how `bcc` beats `body`.

        Ranking by the word list is the whole point: taking the first few
        optional fields in vendor order is the same mistake that gave a real
        account seven ways to delete mail.
        """
        schema = _schema(
            {
                "bcc": _field(),
                "body": _field(),
                "amount": _field("number"),
                "to": _field(),
            }
        )
        offered = _names(model_fields.chosen(schema, limit=3))
        assert "bcc" not in offered
        assert offered.index("to") < offered.index("body")

    def test_the_cap_bounds_what_is_offered(self):
        properties = {name: _field() for name in ("to", "subject", "body", "message")}
        assert len(model_fields.chosen(_schema(properties), limit=2)) == 2

    def test_a_full_required_list_leaves_no_room_for_optional_ones(self):
        schema = _schema(
            {"a": _field(), "b": _field(), "subject": _field()}, required=["a", "b"]
        )
        assert set(_names(model_fields.chosen(schema, limit=2))) == {"a", "b"}


class TestShape:
    def test_a_field_carries_what_a_tool_parameter_needs(self):
        schema = _schema({"to": _field("string", "Who receives it")}, required=["to"])
        assert model_fields.chosen(schema) == [
            {
                "name": "to",
                "type": "string",
                "description": "Who receives it",
                "required": True,
            }
        ]

    def test_an_integer_becomes_a_number(self):
        """`integer` is a JSON Schema type a Decibyl tool may not declare."""
        schema = _schema({"amount": _field("integer")}, required=["amount"])
        assert model_fields.chosen(schema)[0]["type"] == "number"

    def test_a_nullable_union_takes_the_type_that_is_not_null(self):
        schema = _schema({"to": {"type": ["string", "null"]}}, required=["to"])
        assert model_fields.chosen(schema)[0]["type"] == "string"

    def test_an_unknown_type_is_offered_as_text_rather_than_dropped(self):
        schema = _schema({"to": {"type": "uuid"}}, required=["to"])
        assert model_fields.chosen(schema)[0]["type"] == "string"

    def test_a_field_with_no_description_is_never_described_as_nothing(self):
        """A parameter the model is shown with an empty instruction is worse
        than one described by its own name."""
        schema = _schema({"thread_id": _field()}, required=["thread_id"])
        assert model_fields.chosen(schema)[0]["description"] == "thread id"

    def test_a_long_description_is_trimmed(self):
        schema = _schema({"to": _field("string", "x" * 900)}, required=["to"])
        described = model_fields.chosen(schema)[0]["description"]
        assert len(described) == model_fields.MAX_DESCRIPTION


class TestTheRealCase:
    def test_gmail_send_email_becomes_a_handful_of_fields(self):
        """The shape of the live action, cut to what a person dictates."""
        schema = _schema(
            {
                "attachment": _field("array"),
                "bcc": _field("array"),
                "body": _field("string", "The message"),
                "cc": _field("array"),
                "extra_recipients": _field("array"),
                "is_html": _field("boolean"),
                "recipient_email": _field("string", "Who receives it"),
                "subject": _field("string", "The subject line"),
                "thread_id": _field(),
                "user_id": _field(),
            },
            required=["recipient_email"],
        )
        offered = _names(model_fields.chosen(schema))
        assert "recipient_email" in offered
        assert "subject" in offered
        assert "body" in offered
        assert "is_html" not in offered
        assert "thread_id" not in offered
        assert len(offered) <= model_fields.MODEL_FILLS


class TestAWordBeatsASubstring:
    """Found by measuring the real schema, not by reading the code.

    Matching on substring alone, `extra_recipients` outranked `subject`,
    because "recipient" sits early in the word list and happens to appear
    inside it. The model was handed a field almost nobody uses and lost one
    it always needs.
    """

    def test_the_field_that_is_the_word_wins(self):
        schema = _schema({"extra_recipients": _field(), "subject": _field()})
        assert _names(model_fields.chosen(schema, limit=1)) == ["subject"]

    def test_a_token_that_is_the_word_beats_one_that_contains_it(self):
        schema = _schema({"extra_recipients": _field(), "recipient_email": _field()})
        assert _names(model_fields.chosen(schema, limit=1)) == ["recipient_email"]

    def test_the_real_gmail_optionals_come_out_in_the_order_people_dictate(self):
        schema = _schema(
            {
                "attachment": _field("array"),
                "bcc": _field("array"),
                "body": _field(),
                "cc": _field("array"),
                "extra_recipients": _field("array"),
                "is_html": _field("boolean"),
                "subject": _field(),
                "thread_id": _field(),
            }
        )
        assert _names(model_fields.chosen(schema, limit=2)) == ["subject", "body"]
