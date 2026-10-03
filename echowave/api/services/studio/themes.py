"""Design themes for Studio sites: a palette and a pair of typefaces each.

A model left to choose colours picks a blue it half-remembers and puts grey
text on it. These were chosen by hand for the businesses Decibyl serves, and
every pair a section actually draws -- body text on the page, muted text on a
card, label text on a brand button -- is held to WCAG AA contrast by
``tests/test_studio_design.py``, so a theme cannot ship unreadable.

A theme becomes three things in the site: ``src/theme.css`` (Tailwind v4
tokens, so ``bg-brand`` and ``font-display`` mean this theme), ``src/fonts.js``
(the typefaces, bundled from npm by Fontsource -- they ship inside the build,
so nothing loads from a font CDN and the screenshot box renders them too), and
the font packages in ``package.json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

FONT_VERSION = "^5.3.0"


@dataclass(frozen=True)
class Font:
    #: The npm package, e.g. ``@fontsource-variable/inter``.
    package: str
    #: The CSS family name the package registers.
    family: str
    fallback: str = "ui-sans-serif, system-ui, sans-serif"


@dataclass(frozen=True)
class Theme:
    name: str
    label: str
    mood: str
    good_for: tuple[str, ...]
    display: Font
    body: Font
    #: Token -> hex. ``brand-ink`` is text drawn on ``brand``.
    colors: dict[str, str] = field(default_factory=dict)
    radius: str = "1rem"


SANS = "ui-sans-serif, system-ui, sans-serif"
SERIF = "ui-serif, Georgia, serif"

INTER = Font("@fontsource-variable/inter", "Inter Variable")
JAKARTA = Font("@fontsource-variable/plus-jakarta-sans", "Plus Jakarta Sans Variable")
FRAUNCES = Font("@fontsource-variable/fraunces", "Fraunces Variable", SERIF)
DM_SANS = Font("@fontsource-variable/dm-sans", "DM Sans Variable")
MANROPE = Font("@fontsource-variable/manrope", "Manrope Variable")
SPACE = Font("@fontsource-variable/space-grotesk", "Space Grotesk Variable")
OUTFIT = Font("@fontsource-variable/outfit", "Outfit Variable")
PLAYFAIR = Font(
    "@fontsource-variable/playfair-display", "Playfair Display Variable", SERIF
)
SORA = Font("@fontsource-variable/sora", "Sora Variable")
LORA = Font("@fontsource-variable/lora", "Lora Variable", SERIF)
BRICOLAGE = Font(
    "@fontsource-variable/bricolage-grotesque", "Bricolage Grotesque Variable"
)
INSTRUMENT_SANS = Font(
    "@fontsource-variable/instrument-sans", "Instrument Sans Variable"
)
DEVANAGARI = Font(
    "@fontsource-variable/noto-sans-devanagari", "Noto Sans Devanagari Variable"
)


def _palette(brand, brand_ink, ink, muted, canvas, surface, line, accent):
    return {
        "brand": brand,
        "brand-ink": brand_ink,
        "accent": accent,
        "ink": ink,
        "muted": muted,
        "canvas": canvas,
        "surface": surface,
        "line": line,
    }


THEMES: dict[str, Theme] = {
    theme.name: theme
    for theme in (
        Theme(
            "clinic-calm",
            "Clinic calm",
            "Clean, reassuring, clinical without being cold.",
            ("clinics", "dentists", "hospitals", "diagnostics", "physiotherapy"),
            JAKARTA,
            INTER,
            _palette(
                "#0e7490",
                "#ffffff",
                "#0f172a",
                "#475569",
                "#f8fafc",
                "#ffffff",
                "#e2e8f0",
                "#f59e0b",
            ),
        ),
        Theme(
            "warm-kitchen",
            "Warm kitchen",
            "Inviting and handmade: terracotta, cream and a characterful serif.",
            ("bakeries", "restaurants", "cafes", "caterers", "sweet shops"),
            FRAUNCES,
            DM_SANS,
            _palette(
                "#b4532a",
                "#ffffff",
                "#2b1d16",
                "#6b5446",
                "#fbf6ef",
                "#ffffff",
                "#eadfd2",
                "#2f6b4f",
            ),
            radius="1.25rem",
        ),
        Theme(
            "bold-tech",
            "Bold tech",
            "Confident and modern: deep indigo, crisp geometric type.",
            ("software", "startups", "agencies", "IT services", "coaching platforms"),
            SPACE,
            INTER,
            _palette(
                "#4f46e5",
                "#ffffff",
                "#0b1020",
                "#4b5563",
                "#f7f7fb",
                "#ffffff",
                "#e4e4ef",
                "#06b6d4",
            ),
            radius="0.875rem",
        ),
        Theme(
            "luxe-salon",
            "Luxe salon",
            "Elegant and soft: plum, blush and a high-contrast display serif.",
            ("salons", "spas", "boutiques", "jewellers", "bridal"),
            PLAYFAIR,
            MANROPE,
            _palette(
                "#7a2e5c",
                "#ffffff",
                "#21121b",
                "#6b5562",
                "#fbf7f8",
                "#ffffff",
                "#efe2e8",
                "#b08d57",
            ),
            radius="1.5rem",
        ),
        Theme(
            "trusted-finance",
            "Trusted finance",
            "Steady and precise: navy with an emerald signal.",
            ("lenders", "NBFCs", "insurance", "accountants", "wealth advisors"),
            SORA,
            INTER,
            _palette(
                "#1e3a8a",
                "#ffffff",
                "#0b1324",
                "#4a5568",
                "#f6f8fc",
                "#ffffff",
                "#dfe5f0",
                "#047857",
            ),
            radius="0.75rem",
        ),
        Theme(
            "fresh-market",
            "Fresh market",
            "Bright and friendly: leaf green and rounded type.",
            ("grocers", "D2C brands", "pharmacies", "organic stores", "retail"),
            OUTFIT,
            DM_SANS,
            _palette(
                "#15803d",
                "#ffffff",
                "#10231a",
                "#4b5d52",
                "#f6faf6",
                "#ffffff",
                "#dceadf",
                "#ea580c",
            ),
            radius="1.25rem",
        ),
        Theme(
            "bright-campus",
            "Bright campus",
            "Energetic and clear: royal blue with a warm orange.",
            ("coaching institutes", "schools", "edtech", "training centres"),
            BRICOLAGE,
            INSTRUMENT_SANS,
            _palette(
                "#1d4ed8",
                "#ffffff",
                "#0f172a",
                "#475569",
                "#f8fafc",
                "#ffffff",
                "#e2e8f0",
                "#ea580c",
            ),
        ),
        Theme(
            "classic-estate",
            "Classic estate",
            "Established and calm: charcoal, stone and brass.",
            ("real estate", "builders", "interiors", "architects", "hotels"),
            PLAYFAIR,
            MANROPE,
            _palette(
                "#1f2933",
                "#ffffff",
                "#111827",
                "#52606d",
                "#f7f5f2",
                "#ffffff",
                "#e6e1d9",
                "#a16207",
            ),
            radius="0.5rem",
        ),
        Theme(
            "counsel",
            "Counsel",
            "Serious and humane: forest green and a reading serif.",
            ("law firms", "consultants", "CA firms", "NGOs"),
            LORA,
            INTER,
            _palette(
                "#14532d",
                "#ffffff",
                "#0f1f17",
                "#4b5b52",
                "#f7f8f5",
                "#ffffff",
                "#e1e6dd",
                "#b45309",
            ),
            radius="0.5rem",
        ),
        Theme(
            "festive-desi",
            "Festive desi",
            "Warm and celebratory: maroon and saffron, with Devanagari support.",
            (
                "event planners",
                "sweet shops",
                "ethnic wear",
                "temples",
                "Hindi-first sites",
            ),
            BRICOLAGE,
            DEVANAGARI,
            _palette(
                "#9f1239",
                "#ffffff",
                "#1f0d12",
                "#6b4f57",
                "#fdf8f3",
                "#ffffff",
                "#f0e2d8",
                "#d97706",
            ),
            radius="1.25rem",
        ),
    )
}

DEFAULT_THEME = "clinic-calm"

#: Every (foreground, background) pair a section draws, and the contrast it
#: needs: 4.5 for body text, 3 for large display text and icons.
CONTRAST_PAIRS: tuple[tuple[str, str, float], ...] = (
    ("ink", "canvas", 4.5),
    ("ink", "surface", 4.5),
    ("muted", "canvas", 4.5),
    ("muted", "surface", 4.5),
    ("brand-ink", "brand", 4.5),
    ("brand", "canvas", 4.5),
    ("brand", "surface", 4.5),
)


def _luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str) -> float:
    """The WCAG contrast ratio of two hex colours."""
    a, b = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def get(name: str | None) -> Theme:
    theme = THEMES.get((name or "").strip())
    if theme is None:
        raise KeyError(name)
    return theme


def catalogue() -> list[dict[str, object]]:
    """What the model is shown when it chooses."""
    return [
        {
            "name": theme.name,
            "label": theme.label,
            "mood": theme.mood,
            "good_for": list(theme.good_for),
            "fonts": f"{theme.display.family.removesuffix(' Variable')} / "
            f"{theme.body.family.removesuffix(' Variable')}",
            "brand": theme.colors["brand"],
            "accent": theme.colors["accent"],
        }
        for theme in THEMES.values()
    ]


def theme_css(theme: Theme) -> str:
    lines = [
        f"/* Theme: {theme.label}. Written by Studio -- change it by asking for",
        " * another theme, or edit the values here. Each colour is a Tailwind",
        " * token: bg-brand, text-ink, ring-line and so on. */",
        "@theme {",
    ]
    for token, value in theme.colors.items():
        lines.append(f"  --color-{token}: {value};")
    lines.append(
        f'  --font-display: "{theme.display.family}", {theme.display.fallback};'
    )
    lines.append(f'  --font-body: "{theme.body.family}", {theme.body.fallback};')
    lines.append(f"  --radius-card: {theme.radius};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def fonts_js(theme: Theme) -> str:
    packages = list(dict.fromkeys([theme.display.package, theme.body.package]))
    lines = ["// Typefaces, bundled from npm by Fontsource. Written by Studio."]
    lines += [f'import "{package}";' for package in packages]
    return "\n".join(lines) + "\n"


def with_font_dependencies(package_json: str, theme: Theme) -> str:
    """``package_json`` with this theme's fonts and no other Fontsource ones.

    Leaves everything else alone, including packages the model added. A
    package.json that does not parse is returned unchanged; the build will
    say why, which is the right place to hear it.
    """
    try:
        package = json.loads(package_json)
    except ValueError:
        return package_json
    dependencies = {
        name: version
        for name, version in (package.get("dependencies") or {}).items()
        if not name.startswith("@fontsource")
    }
    for font in (theme.display, theme.body):
        dependencies[font.package] = FONT_VERSION
    package["dependencies"] = dict(sorted(dependencies.items()))
    return json.dumps(package, indent=2) + "\n"


def apply(files: dict[str, str], theme: Theme) -> dict[str, str]:
    """``files`` with ``theme`` written into the three places it lives."""
    out = dict(files)
    out["src/theme.css"] = theme_css(theme)
    out["src/fonts.js"] = fonts_js(theme)
    if "package.json" in out:
        out["package.json"] = with_font_dependencies(out["package.json"], theme)
    return out
