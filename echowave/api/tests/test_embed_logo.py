"""The widget logo's two refusals, and the merge that must not lose anything.

These are pure functions on purpose: what they decide — which bytes we are
willing to serve from our origin onto a customer's public page, and what
happens to the rest of a settings blob when a logo is added — is worth being
able to assert without a database or a running app.
"""

import pytest

from api.services.embed_logo import (
    MAX_LOGO_BYTES,
    LogoRejected,
    logo_from_settings,
    logo_storage_key,
    merge_logo_into_settings,
    sniff_image,
    validate_logo,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32


class TestWhatWeAgreeToServe:
    @pytest.mark.parametrize(
        "data,expected",
        [
            (PNG, ("image/png", "png")),
            (JPEG, ("image/jpeg", "jpg")),
            (GIF, ("image/gif", "gif")),
            (WEBP, ("image/webp", "webp")),
        ],
    )
    def test_recognises_the_raster_formats(self, data, expected):
        assert sniff_image(data) == expected

    def test_webp_is_matched_past_its_length_field(self):
        """RIFF carries a four-byte size before the format tag.

        A prefix match on ``RIFF`` alone would accept a WAV file — same
        container, different payload — so the tag at offset 8 is the check that
        matters.
        """
        wav = b"RIFF" + b"\x24\x08\x00\x00" + b"WAVE" + b"\x00" * 32
        with pytest.raises(LogoRejected):
            sniff_image(wav)

    def test_refuses_svg_by_name(self):
        """An SVG is a scripting surface, and the message has to say so.

        It is also the format somebody with a logo is most likely to have, so
        "not an image we recognise" would be actively misleading here.
        """
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>'
        with pytest.raises(LogoRejected, match="SVG"):
            sniff_image(svg)

    def test_refuses_svg_behind_an_xml_declaration(self):
        """Which is how most exported SVGs actually start."""
        svg = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"/>'
        with pytest.raises(LogoRejected, match="SVG"):
            sniff_image(svg)

    def test_refuses_svg_with_leading_whitespace(self):
        with pytest.raises(LogoRejected, match="SVG"):
            sniff_image(b"\n  <svg xmlns='http://www.w3.org/2000/svg'/>")

    def test_refuses_html_pretending_to_be_an_image(self):
        with pytest.raises(LogoRejected):
            sniff_image(b"<html><body>not a logo</body></html>")

    def test_a_declared_content_type_cannot_smuggle_anything_in(self):
        """There is no parameter for it, and that is the design.

        ``sniff_image`` takes bytes only. The multipart part's Content-Type is
        whatever the client typed, so the route never passes it here — this
        test exists so that adding such a parameter later fails deliberately
        rather than quietly.
        """
        with pytest.raises(TypeError):
            sniff_image(b"<svg/>", "image/png")  # type: ignore[call-arg]


class TestSize:
    def test_accepts_a_file_at_the_limit(self):
        at_limit = PNG + b"\x00" * (MAX_LOGO_BYTES - len(PNG))
        assert len(at_limit) == MAX_LOGO_BYTES
        assert validate_logo(at_limit) == ("image/png", "png")

    def test_refuses_one_byte_over(self):
        over = PNG + b"\x00" * (MAX_LOGO_BYTES - len(PNG) + 1)
        with pytest.raises(LogoRejected, match="KB"):
            validate_logo(over)

    def test_refuses_an_empty_file(self):
        with pytest.raises(LogoRejected, match="empty"):
            validate_logo(b"")

    def test_size_is_reported_before_format(self):
        """An oversized JPEG should be told it is oversized.

        Checking format first would answer a 4 MB photo with "not an image we
        recognise", which sends somebody looking for the wrong problem.
        """
        huge = JPEG + b"\x00" * MAX_LOGO_BYTES
        with pytest.raises(LogoRejected, match="KB"):
            validate_logo(huge)


class TestTheStorageKey:
    def test_is_scoped_to_the_organization_and_workflow(self):
        key = logo_storage_key(7, 42, "png")
        assert key.startswith("embed-logos/7/42/")
        assert key.endswith(".png")

    def test_is_different_every_time(self):
        """A stable key would be cached by every browser that already has it.

        Replacing a logo in place shows the previous one to some visitors, on
        the customer's site, for as long as their cache holds — which reads as
        our bug and cannot be cleared from here.
        """
        first = logo_storage_key(7, 42, "png")
        second = logo_storage_key(7, 42, "png")
        assert first != second


class TestMergingIntoSettings:
    def test_keeps_everything_else(self):
        """``update_embed_token`` replaces settings wholesale.

        An upload that wrote only its own field would drop the account's
        colour, button text and post-call card on the floor.
        """
        existing = {
            "buttonColor": "#e8590c",
            "buttonText": "Talk to us",
            "postCall": {"enabled": True, "headline": "Want one?"},
        }
        merged = merge_logo_into_settings(
            existing, key="k", content_type="image/png", backend="2"
        )
        assert merged["buttonColor"] == "#e8590c"
        assert merged["buttonText"] == "Talk to us"
        assert merged["postCall"] == {"enabled": True, "headline": "Want one?"}
        assert merged["logo"] == {
            "key": "k",
            "contentType": "image/png",
            "backend": "2",
        }

    def test_does_not_mutate_the_original(self):
        existing = {"buttonColor": "#e8590c"}
        merge_logo_into_settings(
            existing, key="k", content_type="image/png", backend="2"
        )
        assert "logo" not in existing

    def test_handles_no_settings_at_all(self):
        merged = merge_logo_into_settings(
            None, key="k", content_type="image/png", backend="2"
        )
        assert merged["logo"]["key"] == "k"

    def test_removing_keeps_the_rest(self):
        existing = {"buttonColor": "#e8590c", "logo": {"key": "old"}}
        merged = merge_logo_into_settings(
            existing, key=None, content_type=None, backend=None
        )
        assert merged == {"buttonColor": "#e8590c"}

    def test_removing_when_there_was_none_is_not_an_error(self):
        merged = merge_logo_into_settings(
            {"buttonColor": "#e8590c"}, key=None, content_type=None, backend=None
        )
        assert merged == {"buttonColor": "#e8590c"}


class TestReadingItBack:
    def test_returns_the_logo(self):
        assert logo_from_settings({"logo": {"key": "k"}}) == {"key": "k"}

    @pytest.mark.parametrize(
        "settings",
        [
            None,
            {},
            {"logo": None},
            {"logo": "a-string"},
            {"logo": {}},
            {"logo": {"key": ""}},
            {"logo": {"key": 7}},
            "not-a-dict",
        ],
    )
    def test_anything_unusable_reads_as_absent(self, settings):
        """This runs inside a public endpoint a visitor's browser calls.

        ``settings`` is a free-form dict written by several versions of the
        client, so a shape nobody planned for has to mean "no logo" rather than
        an exception on somebody's website.
        """
        assert logo_from_settings(settings) is None
