"""The switch that makes the widget's typed conversation reachable.

The widget could always hold one -- ``enableText`` has been in
``decibyl-widget.js`` since it was written. It was reachable only by editing
``text=true`` into the script tag by hand, which no screen mentioned, so in
practice the feature shipped and nobody could find it. These tests are about
the flag surviving the trip from the screen to the script.
"""

from types import SimpleNamespace

from api.routes.workflow_embed import generate_embed_script


def _token(settings):
    return SimpleNamespace(token="tok_abc123", settings=settings)


class TestEmbedScriptTextFlag:
    def test_a_widget_asked_for_text_carries_the_flag(self):
        script = generate_embed_script(_token({"enableText": True}))
        assert "text=true" in script

    def test_a_widget_not_asked_for_text_says_nothing_about_it(self):
        """Absence, not ``text=false``. The widget defaults the flag off, and a
        script that names every default is a script nobody can read."""
        script = generate_embed_script(_token({"enableText": False}))
        assert "text=" not in script

    def test_settings_that_never_mentioned_text_are_unchanged(self):
        """Every widget configured before this switch existed. They must keep
        rendering exactly as they did, which means no flag at all."""
        script = generate_embed_script(
            _token({"embedMode": "floating", "buttonText": "Talk to Agent"})
        )
        assert "text=" not in script

    def test_a_token_with_no_settings_at_all_still_renders(self):
        script = generate_embed_script(_token(None))
        assert "text=" not in script
        assert "tok_abc123" in script

    def test_only_a_real_true_turns_it_on(self):
        """Settings are a free-form dict that has been through JSON and a
        database. A truthy string is what a hand-edited row looks like, and
        guessing what somebody meant is how a site gets a feature it did not
        ask for."""
        for value in ("true", "yes", 1, [], {}, None, "false"):
            script = generate_embed_script(_token({"enableText": value}))
            assert "text=true" not in script, f"{value!r} should not enable text"

    def test_the_flag_rides_in_the_script_url(self):
        """Not in the config the widget fetches afterwards: the widget reads
        this while deciding what to build, which happens before that fetch
        returns. Sending it both ways would give two answers that can
        disagree."""
        script = generate_embed_script(_token({"enableText": True}))
        src_line = next(line for line in script.splitlines() if "js.src" in line)
        assert "text=true" in src_line
