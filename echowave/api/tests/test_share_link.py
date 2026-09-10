"""The share page is ours, so a token works there without a whitelist."""

from api.routes import public_embed


def test_our_own_origin_is_always_allowed(monkeypatch):
    monkeypatch.setattr(public_embed, "UI_APP_URL", "https://app.decibyl.ai")
    assert public_embed.is_platform_origin("https://app.decibyl.ai") is True
    assert public_embed.is_platform_origin("https://app.decibyl.ai/talk/abc") is True
    assert public_embed.validate_origin("https://app.decibyl.ai", []) is True


def test_other_origins_still_need_the_whitelist(monkeypatch):
    monkeypatch.setattr(public_embed, "UI_APP_URL", "https://app.decibyl.ai")
    assert public_embed.is_platform_origin("https://evil.example") is False
    assert public_embed.validate_origin("https://evil.example", []) is False
    assert public_embed.validate_origin("", []) is False


def test_no_app_url_means_no_platform_origin(monkeypatch):
    monkeypatch.setattr(public_embed, "UI_APP_URL", "")
    assert public_embed.is_platform_origin("https://app.decibyl.ai") is False
