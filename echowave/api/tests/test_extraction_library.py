"""The catalog has to survive the trip into a node and back out as a prompt.

A library entry is not a display object: on add it is copied into the node's
``qa_extractions`` verbatim, validated as an ``ExtractionSpec``, and rendered
into the QA system prompt. So an entry with a typo'd ``answer_type`` or an
``expected_format`` the renderer does not know does not fail here — it fails on
somebody's call, silently, as an extraction that comes back empty or as prose
where a number was wanted.

These tests are that round trip.
"""

from __future__ import annotations

import pytest

from api.services.workflow.dto import ExtractionSpec, QANodeData
from api.services.workflow.extraction_library import (
    CATALOG_QA_EXTRACTIONS,
    get_library,
)
from api.services.workflow.node_specs import get_spec

#: The values ``ExtractionSpec`` offers in the editor. Written out rather than
#: read off the spec: a test that derives its expectations from the thing under
#: test would pass just as happily if both drifted together.
ANSWER_TYPES = {"free_text", "predefined"}
EXPECTED_FORMATS = {"text", "numeric", "boolean", "timestamp", "email"}


@pytest.fixture(scope="module")
def library():
    return get_library(CATALOG_QA_EXTRACTIONS)


class TestEveryEntryIsUsable:
    def test_each_entry_validates_as_an_extraction_spec(self, library):
        """What the picker adds is what the node stores, field for field."""
        for entry in library.extractions:
            spec = ExtractionSpec(
                name=entry.name,
                prompt=entry.prompt,
                answer_type=entry.answer_type,
                predefined_options=entry.predefined_options,
                expected_format=entry.expected_format,
            )
            assert spec.name == entry.name

    def test_the_whole_library_fits_on_one_node(self, library):
        """The pathological add-everything case, which a demo will do."""
        node = QANodeData(
            name="QA",
            qa_extractions=[
                ExtractionSpec(
                    name=e.name,
                    prompt=e.prompt,
                    answer_type=e.answer_type,
                    predefined_options=e.predefined_options,
                    expected_format=e.expected_format,
                )
                for e in library.extractions
            ],
        )
        assert len(node.qa_extractions) == len(library.extractions)

    def test_answer_types_and_formats_are_ones_the_renderer_knows(self, library):
        """An unknown format does not raise — it silently asks for 'text'.

        ``render_extraction_instructions`` resolves the shape through a dict
        with a default, so a typo here is not an error anywhere. It is an
        extraction that quietly asks the model for the wrong thing.
        """
        for entry in library.extractions:
            assert entry.answer_type in ANSWER_TYPES, entry.key
            if entry.answer_type == "free_text":
                assert entry.expected_format in EXPECTED_FORMATS, entry.key

    def test_a_fixed_set_entry_actually_lists_its_options(self, library):
        """Otherwise the prompt says 'one of your configured options' and means
        nothing, which reads as a working extraction right up until you see the
        answers."""
        for entry in library.extractions:
            if entry.answer_type == "predefined":
                assert entry.predefined_options.strip(), entry.key
                assert "," in entry.predefined_options, entry.key

    def test_names_are_json_keys_already(self, library):
        """``_extraction_key`` sanitizes whatever an operator types, so a
        library name that needs sanitizing would come back under a key that is
        not the one the catalog showed them."""
        for entry in library.extractions:
            assert entry.name == entry.name.strip().lower(), entry.key
            assert entry.name.replace("_", "").isalnum(), entry.key

    def test_keys_and_names_are_unique(self, library):
        keys = [e.key for e in library.extractions]
        names = [e.name for e in library.extractions]
        assert len(keys) == len(set(keys))
        # Two entries sharing a name would collide as JSON keys the moment
        # somebody added both.
        assert len(names) == len(set(names))

    def test_every_entry_is_in_a_listed_category(self, library):
        """The picker renders the sidebar from ``categories``; an entry in a
        category not listed there is unreachable unless someone searches for
        it."""
        listed = set(library.categories)
        for entry in library.extractions:
            assert entry.category in listed, entry.key


class TestTheFieldPointsAtTheCatalog:
    def test_qa_extractions_names_a_catalog_that_exists(self):
        """The wiring, end to end: the property the editor renders carries the
        catalog id, and that id resolves."""
        spec = get_spec("qa")
        assert spec is not None
        prop = next(p for p in spec.properties if p.name == "qa_extractions")

        assert prop.renderer_options is not None
        assert prop.renderer_options.library is not None
        catalog = prop.renderer_options.library.catalog

        assert get_library(catalog).extractions

    def test_an_unknown_catalog_raises(self):
        with pytest.raises(KeyError):
            get_library("not-a-catalog")


class TestTheRowFieldsTheEditorOffers:
    """The picker writes the sub-property names the spec declares.

    It filters what it writes to those names, so a catalog field the running
    spec does not have is dropped rather than saved and rejected. That only
    holds while the two agree about the names.
    """

    def test_the_spec_declares_every_field_an_entry_carries(self, library):
        spec = get_spec("qa")
        prop = next(p for p in spec.properties if p.name == "qa_extractions")
        declared = {p.name for p in prop.properties or []}

        assert {
            "name",
            "prompt",
            "answer_type",
            "predefined_options",
            "expected_format",
        } <= declared
