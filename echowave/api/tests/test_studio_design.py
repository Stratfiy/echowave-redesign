"""Studio's design layer without a database: themes are readable and apply
cleanly, screenshots reach every vendor as pictures and leave the transcript
afterwards, photo search keeps only what may be used, and a website form
submission is cleaned before it reaches an agent."""

from __future__ import annotations

import json

import httpx
import pytest

from api.services.agent_builder import client as builder_client
from api.services.agent_builder.client import IMAGES_KEY, Conversation
from api.services.studio import images, scaffold, sites, themes
from api.services.studio.session import compact

# --- themes ------------------------------------------------------------------


@pytest.mark.parametrize("name", list(themes.THEMES))
def test_every_theme_is_readable_everywhere_a_section_draws(name):
    theme = themes.THEMES[name]
    for foreground, background, needed in themes.CONTRAST_PAIRS:
        ratio = themes.contrast(theme.colors[foreground], theme.colors[background])
        assert ratio >= needed, (name, foreground, background, round(ratio, 2))


def test_contrast_matches_the_wcag_reference_values():
    assert round(themes.contrast("#000000", "#ffffff"), 1) == 21.0
    assert round(themes.contrast("#ffffff", "#ffffff"), 1) == 1.0


def test_every_theme_names_two_fontsource_packages():
    for theme in themes.THEMES.values():
        for font in (theme.display, theme.body):
            assert font.package.startswith("@fontsource")
            assert font.family


def test_applying_a_theme_rewrites_its_three_places_and_keeps_the_rest():
    files = scaffold.starter_files(
        scaffold.FRAMEWORK_VITE_REACT, title="Bakery", theme="clinic-calm"
    )
    package = json.loads(files["package.json"])
    package["dependencies"]["embla-carousel-react"] = "^8.6.0"
    files["package.json"] = json.dumps(package)

    out = themes.apply(files, themes.get("warm-kitchen"))

    assert "--color-brand: #b4532a;" in out["src/theme.css"]
    assert "Fraunces Variable" in out["src/theme.css"]
    assert "@fontsource-variable/fraunces" in out["src/fonts.js"]
    deps = json.loads(out["package.json"])["dependencies"]
    assert "@fontsource-variable/fraunces" in deps
    assert "@fontsource-variable/plus-jakarta-sans" not in deps  # the old theme's
    assert deps["embla-carousel-react"] == "^8.6.0"  # the model's own addition
    assert out["src/App.jsx"] == files["src/App.jsx"]


def test_an_unknown_theme_is_refused():
    with pytest.raises(KeyError):
        themes.get("neon-chaos")


# --- screenshots, to every vendor --------------------------------------------

PNG = "iVBORw0KGgo="


def _conversation_with_a_screenshot():
    return Conversation(
        messages=[
            {"role": "user", "content": "build it"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "a",
                        "name": "review_site_design",
                        "arguments": {"site_id": 1},
                    },
                    {"id": "b", "name": "list_sites", "arguments": {}},
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "a",
                "name": "review_site_design",
                "content": {
                    "page_errors": [],
                    IMAGES_KEY: [
                        {"media_type": "image/png", "data": PNG, "label": "desktop"}
                    ],
                },
            },
            {
                "role": "tool",
                "tool_call_id": "b",
                "name": "list_sites",
                "content": {"sites": []},
            },
        ]
    )


def test_anthropic_gets_the_picture_inside_the_tool_result():
    payload = builder_client._anthropic_request(
        model="m", system="s", conversation=_conversation_with_a_screenshot(), tools=[]
    )
    result = payload["messages"][2]["content"][0]
    assert result["type"] == "tool_result"
    kinds = [block["type"] for block in result["content"]]
    assert kinds == ["text", "image"]
    assert result["content"][1]["source"]["data"] == PNG
    assert PNG not in result["content"][0]["text"]


def test_openai_gets_the_picture_after_the_rounds_last_tool_message():
    payload = builder_client._openai_request(
        model="m", system="s", conversation=_conversation_with_a_screenshot(), tools=[]
    )
    roles = [message["role"] for message in payload["messages"]]
    # Both tool results directly after the call, then the pictures.
    assert roles == ["system", "user", "assistant", "tool", "tool", "user"]
    parts = payload["messages"][-1]["content"]
    assert parts[1]["image_url"]["url"] == f"data:image/png;base64,{PNG}"
    assert PNG not in payload["messages"][3]["content"]


def test_gemini_gets_the_picture_as_inline_data():
    payload = builder_client._gemini_request(
        system="s", conversation=_conversation_with_a_screenshot(), tools=[]
    )
    last = payload["contents"][-1]
    assert last["role"] == "user"
    assert last["parts"][1]["inlineData"] == {"mimeType": "image/png", "data": PNG}
    response = payload["contents"][2]["parts"][0]["functionResponse"]["response"]
    assert IMAGES_KEY not in response


def test_a_round_with_no_pictures_is_sent_exactly_as_before():
    conversation = Conversation(
        messages=[
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "a", "name": "list_sites", "arguments": {}}],
            },
            {
                "role": "tool",
                "tool_call_id": "a",
                "name": "list_sites",
                "content": {"x": 1},
            },
        ]
    )
    payload = builder_client._openai_request(
        model="m", system="s", conversation=conversation, tools=[]
    )
    assert [m["role"] for m in payload["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]


def test_screenshots_leave_the_transcript_once_the_turn_is_over():
    out = compact(_conversation_with_a_screenshot().messages)
    review = out[2]["content"]
    assert IMAGES_KEY not in review
    assert "1 screenshots" in review["screenshots"]
    assert PNG not in json.dumps(out)


# --- photos -------------------------------------------------------------------


def _openverse(results):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["license_type"] == "commercial,modification"
        return httpx.Response(200, json={"results": results})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_photo_search_keeps_usable_photos_with_their_credit():
    found = await images.search(
        "dentist",
        client=_openverse(
            [
                {
                    "url": "https://live.staticflickr.com/a.jpg",
                    "width": 1600,
                    "height": 1067,
                    "title": "Dentist at work",
                    "creator": "A. Photographer",
                    "license": "by",
                    "license_version": "2.0",
                    "attribution": '"Dentist at work" by A. Photographer is licensed under CC BY 2.0.',
                    "foreign_landing_url": "https://flickr.com/photos/a",
                },
                # No attribution on a licence that needs one: dropped.
                {"url": "https://x.org/b.jpg", "width": 2000, "license": "by-sa"},
                # Not https: dropped.
                {"url": "http://x.org/c.jpg", "width": 2000, "license": "cc0"},
                # Public domain needs no credit.
                {
                    "url": "https://x.org/d.jpg",
                    "width": 1200,
                    "license": "cc0",
                    "title": "Chair",
                },
            ]
        ),
    )
    assert [f["url"] for f in found] == [
        "https://live.staticflickr.com/a.jpg",
        "https://x.org/d.jpg",
    ]
    assert found[0]["needs_credit"] is True
    assert found[0]["credit"]["url"] == "https://flickr.com/photos/a"
    assert found[1]["needs_credit"] is False


async def test_small_photos_are_used_only_when_nothing_bigger_exists():
    found = await images.search(
        "x",
        client=_openverse(
            [
                {"url": "https://x.org/small.jpg", "width": 300, "license": "cc0"},
                {"url": "https://x.org/big.jpg", "width": 1400, "license": "cc0"},
            ]
        ),
    )
    assert [f["url"] for f in found] == ["https://x.org/big.jpg"]


async def test_an_unreachable_photo_library_is_said_plainly():
    def down(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(images.ImageSearchError, match="could not be reached"):
        await images.search(
            "x", client=httpx.AsyncClient(transport=httpx.MockTransport(down))
        )
    with pytest.raises(images.ImageSearchError):
        await images.search("   ")


# --- form submissions ---------------------------------------------------------


def test_a_submission_is_cleaned_before_it_reaches_an_agent():
    fields = sites.clean_submission(
        {
            "Name": "Asha",
            "phone": "+91 98765 43210",
            "message": "x" * 5000,
            "empty": "",
            "website": "",
            "weird key!": "kept as weirdkey",
        }
    )
    assert fields["name"] == "Asha"
    assert len(fields["message"]) == sites.MAX_FORM_VALUE_CHARS
    assert "empty" not in fields and "website" not in fields
    assert fields["weirdkey"] == "kept as weirdkey"


def test_a_filled_honeypot_is_a_bot_and_goes_nowhere():
    assert sites.clean_submission({"name": "x", "website": "http://spam"}) is None


@pytest.mark.parametrize("raw", [[], "text", {}, {"name": "  "}])
def test_nothing_usable_is_refused(raw):
    with pytest.raises(sites.FormRejected):
        sites.clean_submission(raw)


def test_the_form_endpoint_is_written_as_a_js_string():
    js = scaffold.config_js('https://api.example.com/x"; alert(1); "')
    assert js.count("export const FORM_ENDPOINT = ") == 1
    assert '\\"; alert(1); \\"' in js
