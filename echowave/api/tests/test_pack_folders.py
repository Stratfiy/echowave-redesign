"""Packs as folders: the eight live packs round-trip, the drafts load.

Step 8's two checks, as tests:

* A pack installed from a folder path is the same worker the catalogue
  installs -- same ``AgentPack``, same template, byte-identical workflow.
* The prompt-injection rule reaches every node of every pack loaded from a
  folder, live and draft, composed the way the engine composes it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from api.services.agent_templates import get_template
from api.services.agent_templates.catalogue import _registered, unregister_template
from api.services.agent_templates.materialise import to_workflow_definition
from api.services.packs.catalogue import _packs, all_packs
from api.services.packs.folder import (
    DRAFTS_DIR,
    LIVE_DIR,
    FolderPack,
    PackFolderError,
    Source,
    check_drift,
    draft_folders,
    live_folders,
    load,
    render,
)
from api.services.workflow import untrusted
from api.services.workflow.pipecat_engine_context_composer import (
    compose_system_prompt_for_node,
)

DEMO = {"demo_number": "+911234567890", "demo_url": "https://decibyl.test/demo"}


def _composed_rule_missing(folder: FolderPack) -> list[str]:
    """Nodes of the folder's template whose composed prompt lacks the rule."""
    missing = []
    workflow = SimpleNamespace(global_node_id=None, nodes={})
    for node in folder.template.nodes:
        if not node.prompt:
            continue
        composed = compose_system_prompt_for_node(
            node=SimpleNamespace(
                id=node.name, prompt=node.prompt, add_global_prompt=False
            ),
            workflow=workflow,
            format_prompt=lambda s: s,
            has_recordings=False,
        )
        if untrusted.RULE not in composed:
            missing.append(f"{folder.pack.slug}:{node.name}")
    return missing


class TestLiveFoldersEqualTheCatalogue:
    def test_every_catalogue_pack_has_a_folder_and_it_is_current(self):
        pairs = [(p, get_template(p.template_id)) for p in _packs(None, None)]
        assert len(pairs) == 8
        assert check_drift(pairs, LIVE_DIR) == [], (
            "packs/live has drifted from the catalogue; "
            "run: python -m scripts.export_pack_folders"
        )

    @pytest.mark.parametrize("demo", [DEMO, {}])
    def test_folder_installs_the_same_worker(self, demo):
        folders = {f.pack.slug: f for f in live_folders(**demo)}
        catalogue = _packs(demo.get("demo_number"), demo.get("demo_url"))
        assert set(folders) == {p.slug for p in catalogue}
        for pack in catalogue:
            folder = folders[pack.slug]
            assert folder.pack == pack
            assert folder.template == pack.template
            assert folder.workflow_definition() == to_workflow_definition(pack.template)
            assert folder.draft is False

    def test_the_folder_registers_nothing_new(self):
        live_folders()
        assert not any(p.template_id in _registered for p in all_packs())

    def test_the_rule_reaches_every_node(self):
        missing = []
        for folder in live_folders(**DEMO):
            missing.extend(_composed_rule_missing(folder))
        assert missing == []


class TestDraftFolders:
    def test_the_prompt_pack_loaded_as_drafts(self):
        drafts = draft_folders()
        assert len(drafts) == 26
        for draft in drafts:
            assert draft.draft is True
            assert draft.pack.listed is False
            assert draft.source.repo == "Stratfiy/decibyl"
            assert len(draft.source.ref) == 40, "pinned to a commit, not a branch"
            assert draft.source.path.startswith("docs/product/prompts/")
            assert "tests.md" in draft.references
            assert draft.template.id.startswith("draft_")
            # It materialises into something the editor can open.
            definition = draft.workflow_definition()
            assert {n["type"] for n in definition["nodes"]} >= {"startCall", "endCall"}

    def test_drafts_never_reach_the_shelf(self):
        from api.services.agent_templates import list_templates

        draft_folders()
        shelf = {p.slug for p in all_packs()}
        gallery = {t.id for t in list_templates()}
        for draft in draft_folders():
            assert draft.template.id not in gallery
            # A draft sharing a slug with a live pack is still the live pack
            # on the shelf: the catalogue does not read folders.
            if draft.pack.slug in shelf:
                assert draft.pack.template_id != next(
                    p.template_id for p in all_packs() if p.slug == draft.pack.slug
                )

    def test_the_rule_reaches_every_node(self):
        missing = []
        for folder in draft_folders():
            missing.extend(_composed_rule_missing(folder))
        assert missing == []

    def test_every_variable_in_the_prompt_is_declared(self):
        import re

        for draft in draft_folders():
            used = set(re.findall(r"\{\{([a-z_]+)\}\}", draft.skill.body))
            assert used <= set(draft.template.template_variables), draft.pack.slug

    def test_tests_stay_out_of_the_prompt(self):
        for draft in draft_folders():
            assert "## Tests" not in draft.skill.body
            assert "### Tests" not in draft.skill.body
            assert draft.references["tests.md"].startswith("## Tests")


class TestTheLoaderRefuses:
    def _write(self, tmp_path: Path, text: str) -> Path:
        folder = tmp_path / "some_pack"
        folder.mkdir()
        (folder / "SKILL.md").write_text(text, encoding="utf-8")
        return folder

    def test_a_folder_with_no_skill_file(self, tmp_path):
        with pytest.raises(PackFolderError, match="has no SKILL.md"):
            load(tmp_path)

    def test_a_skill_that_is_not_a_pack(self, tmp_path):
        folder = self._write(
            tmp_path, "---\nname: some-pack\ndescription: x\n---\nDo things.\n"
        )
        with pytest.raises(PackFolderError, match="no 'decibyl' block"):
            load(folder)

    def test_a_newer_format(self, tmp_path):
        folder = self._write(
            tmp_path,
            "---\nname: some-pack\ndescription: x\ndecibyl:\n  format: 99\n---\n## A\nx\n",
        )
        with pytest.raises(PackFolderError, match="pack format 99"):
            load(folder)

    def test_a_body_whose_sections_do_not_match_the_declared_nodes(self, tmp_path):
        live = (LIVE_DIR / "internal_knowledge" / "SKILL.md").read_text()
        broken = live.replace("## Take the question", "## Something else")
        folder = self._write(tmp_path, broken)
        with pytest.raises(PackFolderError, match="must match, in order"):
            load(folder)

    def test_drift_from_the_catalogue_is_named(self, tmp_path):
        live = (LIVE_DIR / "internal_knowledge" / "SKILL.md").read_text()
        drifted = live.replace("## Close\n", "## Close\nSay goodbye in Latin.\n", 1)
        folder = self._write(tmp_path, drifted)
        with pytest.raises(
            PackFolderError, match=r"differs from it at nodes\[1\]\.prompt"
        ):
            load(folder)

    def test_a_name_that_is_not_the_slug(self, tmp_path):
        live = (LIVE_DIR / "internal_knowledge" / "SKILL.md").read_text()
        folder = self._write(
            tmp_path, live.replace("name: internal-knowledge", "name: other")
        )
        with pytest.raises(PackFolderError, match="must be the slug with hyphens"):
            load(folder)

    def test_a_folder_template_cannot_shadow_the_catalogue(self):
        template = get_template("internal_knowledge")
        from api.services.agent_templates.catalogue import register_template

        with pytest.raises(ValueError, match="already in the catalogue"):
            register_template(template)


class TestRenderRoundTrip:
    def test_a_draft_source_and_flag_survive(self, tmp_path):
        draft = next(iter(draft_folders()))
        text = render(draft.pack, draft.template, source=draft.source, draft=True)
        assert text == (draft.path / "SKILL.md").read_text(encoding="utf-8")

    def test_a_pack_whose_prompt_reads_as_a_heading_is_refused(self):
        pack = _packs(None, None)[0]
        template = get_template(pack.template_id)
        node = template.nodes[0].model_copy(update={"prompt": "## not allowed"})
        bad = template.model_copy(update={"nodes": [node, *template.nodes[1:]]})
        with pytest.raises(PackFolderError, match="reads as a heading"):
            render(pack, bad)

    def test_a_folder_loaded_from_elsewhere_registers_and_unregisters(self, tmp_path):
        draft = next(iter(draft_folders()))
        text = (draft.path / "SKILL.md").read_text(encoding="utf-8")
        folder = tmp_path / draft.pack.slug
        folder.mkdir()
        (folder / "SKILL.md").write_text(text.replace(draft.template.id, "draft_tmp_x"))
        loaded = load(folder)
        assert loaded.template.id == "draft_tmp_x"
        assert get_template("draft_tmp_x") is not None
        unregister_template("draft_tmp_x")
        assert get_template("draft_tmp_x") is None
        assert loaded.source == Source(
            repo="Stratfiy/decibyl",
            ref=draft.source.ref,
            path=draft.source.path,
            licence=draft.source.licence,
        )


def test_the_folder_roots_exist():
    assert DRAFTS_DIR.is_dir()
    assert LIVE_DIR.is_dir()
