"""The gate on its own: every step judged before it is taken.

Pure functions, no browser. The session and injection tests run the same
rules through a whole task; these pin the individual decisions.
"""

from __future__ import annotations

import pytest

from api.services.browser import gate


def _task(
    request="Check my bill on bills.example.in", verbs=None, sites=("bills.example.in",)
):
    return gate.Task(
        request=request,
        task=request,
        sites=list(sites),
        verbs=gate.verbs_asked(request) if verbs is None else list(verbs),
        rules=[],
    )


def _press(text, *, tag="button", type_="submit", in_form=True, **extra):
    return {
        "action": "click",
        "url": "https://bills.example.in/pay",
        "element": {
            "tag": tag,
            "type": type_,
            "text": text,
            "in_form": in_form,
            **extra,
        },
    }


class TestWhatThePersonAsked:
    @pytest.mark.parametrize(
        "line,verbs",
        [
            ("Check my bill on bescom.co.in", []),
            ("Find the cheapest basmati across amazon.in and flipkart.com", []),
            ("Pay my electricity bill", ["pay"]),
            ("Fill this form and submit it", ["submit"]),
            ("Book a table for two at Toit", ["book"]),
            ("Sign up for the newsletter", ["sign_up"]),
            ("Reply to the seller and ask about the size", ["send"]),
            ("Check my order status", []),
            ("My email is a@b.com, check my booking", []),
        ],
    )
    def test_verbs_come_from_the_persons_words(self, line, verbs):
        assert gate.verbs_asked(line) == verbs

    def test_the_model_cannot_add_a_verb_the_person_did_not_say(self):
        assert gate.allowed_verbs("Check my bill", ["pay", "send"]) == []
        assert gate.allowed_verbs("Pay my bill", ["pay", "send"]) == ["pay"]


class TestPresses:
    def test_a_payment_asked_for_is_carded_with_the_exact_button(self):
        verdict = gate.check(
            _press("Pay ₹2,340"), _task("Pay my bill on bills.example.in")
        )
        assert verdict.decision == gate.ASK
        assert verdict.verb == gate.PAY
        assert verdict.label == "Pay: press “Pay ₹2,340” on bills.example.in"

    def test_a_payment_not_asked_for_is_refused(self):
        verdict = gate.check(_press("Pay ₹2,340"), _task())
        assert verdict.decision == gate.REFUSE
        assert "did not ask" in verdict.reason

    @pytest.mark.parametrize(
        "text", ["Search", "Sign in", "Next", "Accept cookies", "View bill", "Continue"]
    )
    def test_harmless_presses_just_happen(self, text):
        assert gate.check(_press(text), _task()).decision == gate.ALLOW

    def test_a_search_form_is_not_a_submission(self):
        request = _press("Go", form_is_search=True, form_fields={"q": "rice"})
        assert gate.check(request, _task()).decision == gate.ALLOW

    def test_an_unnamed_submit_is_asked_about_not_guessed(self):
        verdict = gate.check(_press("Get"), _task())
        assert verdict.decision == gate.ASK and verdict.verb == gate.SUBMIT

    def test_enter_in_a_form_is_a_press(self):
        request = {
            "action": "send_keys",
            "keys": "Enter",
            "url": "https://bills.example.in/c",
            "element": {"tag": "input", "name": "comment", "in_form": True},
        }
        assert gate.check(request, _task()).decision == gate.REFUSE

    def test_a_link_off_the_task_is_refused(self):
        request = _press(
            "Offer", tag="a", type_="", in_form=False, href="https://evil.example/"
        )
        assert gate.check(request, _task()).decision == gate.REFUSE

    def test_a_form_posting_off_the_task_is_refused(self):
        request = _press("Verify", form_action="https://evil.example/steal")
        assert gate.check(request, _task()).decision == gate.REFUSE


class TestTyping:
    def _type(self, text, **element):
        return {
            "action": "input",
            "url": "https://bills.example.in/",
            "text": text,
            "element": {"tag": "input", **element},
        }

    @pytest.mark.parametrize(
        "element",
        [
            {"type": "password", "name": "pw"},
            {"name": "login_pin"},
            {"name": "otp"},
            {"autocomplete": "one-time-code"},
            {"name": "cardnumber"},
            {"autocomplete": "cc-csc"},
        ],
    )
    def test_secrets_are_never_typed(self, element):
        verdict = gate.check(self._type("123456", **element), _task())
        assert verdict.decision == gate.REFUSE
        assert "Take over" in verdict.reason

    def test_a_card_number_is_refused_wherever_it_goes(self):
        verdict = gate.check(self._type("4111 1111 1111 1111", name="notes"), _task())
        assert verdict.decision == gate.REFUSE

    def test_details_the_person_gave_may_be_typed(self):
        task = _task(
            "Check my bill on bills.example.in, consumer 12345, email asha@example.com"
        )
        assert (
            gate.check(self._type("asha@example.com", name="email"), task).decision
            == gate.ALLOW
        )

    def test_details_the_person_did_not_give_are_refused(self):
        verdict = gate.check(self._type("9123456780", name="mobile"), _task())
        assert verdict.decision == gate.REFUSE


class TestNavigation:
    def test_data_from_the_task_cannot_ride_out_in_a_link(self):
        task = _task("Check my bill on bills.example.in, email asha@example.com")
        request = {
            "action": "navigate",
            "url": "https://bills.example.in/",
            "target_url": "https://bills.example.in/t?e=asha%40example.com",
        }
        assert gate.check(request, task).decision == gate.REFUSE

    def test_a_search_may_leave_the_named_sites(self):
        request = {"action": "search", "target_url": "https://duckduckgo.com/?q=bescom"}
        assert gate.check(request, _task()).decision == gate.ALLOW

    @pytest.mark.parametrize("action", sorted(gate.NEVER))
    def test_some_steps_are_never_taken(self, action):
        assert gate.check({"action": action}, _task()).decision == gate.REFUSE

    def test_an_unknown_step_is_refused_by_name(self):
        verdict = gate.check({"action": "teleport"}, _task())
        assert verdict.decision == gate.REFUSE and "teleport" in verdict.reason


class TestTheCard:
    def test_the_card_masks_secrets_again_whatever_the_box_sent(self):
        element = gate.Element.of(
            {
                "form_fields": {
                    "name": "Asha",
                    "password": "hunter22",
                    "card": "4111111111111111",
                }
            }
        )
        shown = {f["name"]: f["value"] for f in gate.fields_shown(element)}
        assert shown["name"] == "Asha"
        assert shown["password"] == "••••••"
        assert "4111111111111111" not in shown["card"]

    def test_the_digest_binds_the_exact_step(self):
        a = _press("Pay ₹2,340")
        b = _press("Pay ₹23,400")
        assert gate.digest(a) == gate.digest(dict(a))
        assert gate.digest(a) != gate.digest(b)


class TestTheCardDoesNotInventValues:
    def test_an_empty_secret_field_reads_as_empty_not_as_dots(self):
        """Found in phase 3: the card showed "password ••••••" for a password
        field nobody had typed in, so it claimed the form sent a secret it
        did not. Masking hides a value; it must not invent one."""
        element = gate.Element.of({"form_fields": {"name": "Asha", "password": "", "otp": ""}})
        shown = {f["name"]: f["value"] for f in gate.fields_shown(element)}
        assert shown == {"name": "Asha", "password": "", "otp": ""}

    def test_a_filled_secret_field_is_still_masked(self):
        element = gate.Element.of({"form_fields": {"password": "hunter22"}})
        assert gate.fields_shown(element) == [{"name": "password", "value": "••••••"}]
