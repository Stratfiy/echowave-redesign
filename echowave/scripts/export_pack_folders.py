"""Write the catalogue's packs to ``packs/live/<slug>/SKILL.md``, or check them.

    python -m scripts.export_pack_folders           # (re)write every folder
    python -m scripts.export_pack_folders --check   # exit 1 naming stale ones

The catalogue in ``api/services/packs/catalogue.py`` is what runs; the
folders are the same packs in the published skills layout, so a person can
read a pack, and a folder loader can install one without a code change.
``test_pack_folders.py`` runs the check, so a catalogue edit without a
regenerate fails CI with the slug that drifted.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from api.services.agent_templates import get_template
from api.services.packs.catalogue import _packs
from api.services.packs.folder import LIVE_DIR, SKILL_FILE, check_drift, render


def catalogue_pairs():
    """Every catalogue pack with its template, demo line left blank.

    The demo number and share link are not written to a folder -- they are
    the deployment's -- so the catalogue is built with none, which is also the
    build ``all_packs`` makes on a box with no demo configured.
    """
    for pack in _packs(None, None):
        template = get_template(pack.template_id)
        assert template is not None
        yield pack, template


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="report drift, write nothing"
    )
    parser.add_argument("--root", type=Path, default=LIVE_DIR)
    args = parser.parse_args(argv)

    pairs = list(catalogue_pairs())
    if args.check:
        stale = check_drift(pairs, args.root)
        if stale:
            print(
                "stale pack folders: "
                + ", ".join(stale)
                + "\nrun: python -m scripts.export_pack_folders",
                file=sys.stderr,
            )
            return 1
        print(f"{len(pairs)} pack folders match the catalogue")
        return 0

    for pack, template in pairs:
        folder = args.root / pack.slug
        folder.mkdir(parents=True, exist_ok=True)
        (folder / SKILL_FILE).write_text(render(pack, template), encoding="utf-8")
        print(f"wrote {folder / SKILL_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
