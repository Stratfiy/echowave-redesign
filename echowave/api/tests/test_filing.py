"""Filing a document into Drive (A3): class folder, consistent name, one
question when unclear, never a guessed owner.

Step 13's check is the three sample documents at the bottom: an insurance
PDF and a rent agreement land in their folders under their names on
reading; an Aadhaar photo is asked about, is not filed on the model's
reading of the holder, and lands under the name the person gave.

Eval scenario: documents_three_samples_filed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.enums import AgentEventKind
from api.services.workflow import documents, filing

ORG = 7
WHEN = datetime(2026, 9, 15, 10, 30, tzinfo=UTC)


class TestNaming:
    def test_class_subject_date_extension(self):
        assert (
            filing.filing_name(
                "insurance", "Policy LIC-77", WHEN, filename="scan.PDF", mime_type=None
            )
            == "Insurance - Policy LIC-77 - 2026-09-15.pdf"
        )

    def test_no_subject_is_class_and_date(self):
        assert (
            filing.filing_name("property", None, WHEN, filename="x.pdf", mime_type=None)
            == "Property - 2026-09-15.pdf"
        )

    def test_extension_comes_from_the_mime_when_the_name_has_none(self):
        assert filing.filing_name(
            "identity", "Aadhaar - Meera", WHEN, filename="img", mime_type="image/jpeg"
        ).endswith(" - 2026-09-15.jpg")

    def test_path_characters_in_a_subject_are_cleaned(self):
        assert (
            filing.filing_name(
                "finance",
                'Invoice/2026: "Acme"',
                WHEN,
                filename="a.pdf",
                mime_type=None,
            )
            == "Finance - Invoice 2026 Acme - 2026-09-15.pdf"
        )


class TestDeciding:
    def test_an_insurance_pdf_is_filed_under_its_policy(self):
        d = filing.decide(
            filename="LIC policy.pdf",
            text="…",
            proposed={"policy_number": "LIC-77", "expiry_date": "2027-03-31"},
        )
        assert d.ready and d.kind == "insurance" and d.subject == "Policy LIC-77"

    def test_a_rent_agreement_keeps_its_own_name(self):
        d = filing.decide(filename="Rent agreement Hosur flat.pdf", text="")
        assert d.ready and d.kind == "property"
        assert d.subject == "Rent agreement Hosur flat"

    def test_a_phone_named_file_is_classified_from_its_text(self):
        d = filing.decide(
            filename="WhatsApp image 3f2a9c1d.jpg",
            text="Government of India. Unique Identification Authority. Aadhaar 1234 5678 9012 Meera Iyer",
        )
        assert d.kind == "identity"
        assert not d.ready
        assert d.question.startswith("Whose Aadhaar is this")
        assert "never guess" in d.question

    def test_an_identity_document_never_takes_the_models_reading_of_the_holder(self):
        d = filing.decide(
            filename="aadhaar.jpg",
            text="",
            proposed={"holder_name": "Meera Iyer", "document_number": "1234 5678 9012"},
        )
        assert not d.ready and d.subject is None

    def test_a_confirmed_holder_names_the_identity_document(self):
        d = filing.decide(
            filename="aadhaar.jpg",
            text="",
            proposed={"holder_name": "Meera Iyer"},
            confirmed={"holder_name": "Meera Iyer"},
        )
        assert d.ready and d.subject == "Aadhaar - Meera Iyer"

    def test_something_unrecognisable_is_asked_about_once(self):
        d = filing.decide(filename="IMG_4471.jpg", text="lorem ipsum")
        assert d.kind == "other" and not d.ready
        assert "What should I file it as" in d.question
        for kind in filing.KINDS:
            assert kind in d.question


def _document(**overrides):
    base = dict(
        id=55,
        organization_id=ORG,
        document_uuid="c0ffee11-0000-0000-0000-000000000001",
        filename="LIC policy.pdf",
        mime_type="application/pdf",
        full_text="Policy LIC-77 …",
        created_at=WHEN,
        custom_metadata={
            "source": "whatsapp",
            "from": "+919876543210",
            "s3_key": "documents/7/x/LIC policy.pdf",
            "fields_proposed": {"policy_number": "LIC-77"},
        },
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _Drive:
    """A Drive that remembers what was asked of it: folders by name under
    a parent, uploads by folder."""

    def __init__(self):
        self.folders: dict[tuple[str | None, str], str] = {}
        self.uploads: list[dict] = []
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, organization_id, tool, arguments, *, ref_id):
        self.calls.append((tool, arguments))
        if tool == filing.FIND_FILE_TOOL:
            key = (arguments.get("parent"), arguments["name_exact"])
            if key in self.folders:
                return {
                    "files": [
                        {
                            "id": self.folders[key],
                            "name": arguments["name_exact"],
                            "mimeType": filing.FOLDER_MIME,
                        }
                    ]
                }
            return {"files": []}
        if tool == filing.CREATE_FOLDER_TOOL:
            key = (arguments.get("parent_id"), arguments["name"])
            self.folders[key] = f"folder-{len(self.folders) + 1}"
            return {"id": self.folders[key]}
        if tool == filing.UPLOAD_TOOL:
            self.uploads.append(arguments)
            return {
                "file": {
                    "id": f"file-{len(self.uploads)}",
                    "webViewLink": f"https://drive.example/{len(self.uploads)}",
                }
            }
        raise AssertionError(tool)

    def folder_of(self, upload: dict) -> str:
        by_id = {v: k for k, v in self.folders.items()}
        parent, name = by_id[upload["folder_to_upload_to"]]
        root = by_id[parent][1] if parent else None
        return f"{root}/{name}" if root else name


def _filing_patches(
    drive: _Drive, record: AsyncMock, merge: AsyncMock, reply: AsyncMock
):
    storage = SimpleNamespace(aread_bytes=AsyncMock(return_value=b"%PDF-1.4 bytes"))
    return (
        patch.object(documents, "_drive", drive),
        patch("api.services.storage.get_storage", return_value=storage),
        patch.object(
            filing,
            "_upload_slot",
            AsyncMock(
                side_effect=lambda **kw: {
                    "name": kw["filename"],
                    "mimetype": kw["mime_type"],
                    "s3key": "slot/1",
                }
            ),
        ),
        patch.object(filing.db_client, "merge_document_custom_metadata", merge),
        patch.object(filing.agent_timeline, "record", record),
        patch("api.services.messaging.whatsapp_inbound.reply", reply),
    )


@pytest.mark.asyncio
class TestFilingOnRead:
    async def test_the_folder_is_made_once_and_reused(self):
        drive, record, merge, reply = _Drive(), AsyncMock(), AsyncMock(), AsyncMock()
        first = _document()
        second = _document(id=56, document_uuid="c0ffee11-0000-0000-0000-000000000002")
        with (
            patch.object(
                filing.db_client,
                "get_document_by_id",
                AsyncMock(side_effect=[first, second]),
            ),
            ExitStackPatches(_filing_patches(drive, record, merge, reply)),
        ):
            await filing.on_read(ORG, 55)
            await filing.on_read(ORG, 56)
        creates = [a for t, a in drive.calls if t == filing.CREATE_FOLDER_TOOL]
        assert [a["name"] for a in creates] == ["Decibyl", "Insurance"]
        assert len(drive.uploads) == 2

    async def test_a_drive_that_will_not_take_it_is_a_line_not_a_crash(self):
        record, merge, reply = AsyncMock(), AsyncMock(), AsyncMock()
        document = _document()

        async def refuse(*a, **k):
            raise documents.DocumentError("Google Drive is not connected here.")

        with (
            patch.object(
                filing.db_client, "get_document_by_id", AsyncMock(return_value=document)
            ),
            patch.object(documents, "_drive", refuse),
            patch.object(filing.db_client, "merge_document_custom_metadata", merge),
            patch.object(filing.agent_timeline, "record", record),
            patch("api.services.messaging.whatsapp_inbound.reply", reply),
        ):
            result = await filing.on_read(ORG, 55)
        assert result["status"] == "error"
        assert record.await_args.kwargs["kind"] == AgentEventKind.COULD_NOT.value
        assert "not connected" in record.await_args.kwargs["payload"]["body"]
        merge.assert_not_awaited()

    async def test_an_already_filed_document_is_left_alone(self):
        document = _document(custom_metadata={"filed": {"name": "x"}})
        with patch.object(
            filing.db_client, "get_document_by_id", AsyncMock(return_value=document)
        ):
            assert await filing.on_read(ORG, 55) == {"status": "already_filed"}


@pytest.mark.asyncio
class TestThreeSampleDocuments:
    """Step 13's check: three documents land in the right folders with the
    right names, and the identity one only after a person named its owner."""

    async def test_documents_three_samples_filed(self):
        drive, record, merge, reply = _Drive(), AsyncMock(), AsyncMock(), AsyncMock()
        insurance = _document()
        rent = _document(
            id=56,
            document_uuid="c0ffee11-0000-0000-0000-000000000002",
            filename="Rent agreement Hosur flat.pdf",
            full_text="This rent agreement …",
            custom_metadata={
                "source": "whatsapp",
                "from": "+919876543210",
                "s3_key": "documents/7/y/rent.pdf",
                "fields_proposed": {},
            },
        )
        aadhaar = _document(
            id=57,
            document_uuid="c0ffee11-0000-0000-0000-000000000003",
            filename="WhatsApp image 3f2a9c1d.jpg",
            mime_type="image/jpeg",
            full_text="Government of India Aadhaar 1234 5678 9012 Meera Iyer",
            custom_metadata={
                "source": "whatsapp",
                "from": "+919876543210",
                "s3_key": "documents/7/z/img.jpg",
                "fields_proposed": {
                    "holder_name": "Meera Iyer",
                    "document_number": "1234 5678 9012",
                },
            },
        )
        by_id = {55: insurance, 56: rent, 57: aadhaar}

        with (
            patch.object(
                filing.db_client,
                "get_document_by_id",
                AsyncMock(side_effect=lambda i: by_id[i]),
            ),
            ExitStackPatches(_filing_patches(drive, record, merge, reply)),
        ):
            one = await filing.on_read(ORG, 55)
            two = await filing.on_read(ORG, 56)
            three = await filing.on_read(ORG, 57)

            # 1. Insurance, filed on reading under its policy number.
            assert one["status"] == "success"
            assert one["name"] == "Insurance - Policy LIC-77 - 2026-09-15.pdf"
            assert one["folder"] == "Decibyl/Insurance"
            # 2. Property, filed on reading under the name it came with.
            assert two["status"] == "success"
            assert (
                two["name"] == "Property - Rent agreement Hosur flat - 2026-09-15.pdf"
            )
            assert two["folder"] == "Decibyl/Property"
            # 3. Identity: asked, not filed, and the model's reading of the
            #    holder is not used.
            assert three["status"] == "asked"
            assert three["question"].startswith("Whose Aadhaar is this")
            assert len(drive.uploads) == 2
            asked_on_whatsapp = [c.kwargs["body"] for c in reply.await_args_list]
            assert any("Whose Aadhaar" in b for b in asked_on_whatsapp)
            assert not any("Meera" in b for b in asked_on_whatsapp)
            marks = [c.kwargs["patch"] for c in merge.await_args_list]
            assert any(filing.ASKED_KEY in m for m in marks)

            # Asked once: a second read does not ask again.
            aadhaar.custom_metadata[filing.ASKED_KEY] = "2026-09-15T10:31:00+00:00"
            reply.reset_mock()
            again = await filing.on_read(ORG, 57)
            assert again == {"status": "asked"}
            reply.assert_not_awaited()

            # The person answers on the thread; the model files it with the
            # name they gave.
            with (
                patch.object(
                    filing.db_client,
                    "get_document_by_uuid",
                    AsyncMock(return_value=None),
                ),
                patch.object(
                    filing.db_client,
                    "find_document_by_uuid_prefix",
                    AsyncMock(return_value=aadhaar),
                ),
            ):
                filed = await filing.file_for_thread(
                    ORG,
                    {
                        "document_uuid": "c0ffee11",
                        "kind": "identity",
                        "subject": "Meera Iyer",
                    },
                )
            assert filed["status"] == "success"
            assert filed["name"] == "Identity - Aadhaar - Meera Iyer - 2026-09-15.jpg"
            assert filed["folder"] == "Decibyl/Identity"

        # Where each landed, as Drive saw it.
        landed = {
            u["file_to_upload"]["name"]: drive.folder_of(u) for u in drive.uploads
        }
        assert landed == {
            "Insurance - Policy LIC-77 - 2026-09-15.pdf": "Decibyl/Insurance",
            "Property - Rent agreement Hosur flat - 2026-09-15.pdf": "Decibyl/Property",
            "Identity - Aadhaar - Meera Iyer - 2026-09-15.jpg": "Decibyl/Identity",
        }
        # Every filing is a receipt line with the folder and the link.
        receipts = [
            c.kwargs["payload"]["body"]
            for c in record.await_args_list
            if c.kwargs["kind"] == AgentEventKind.AGENT_ACTED.value
        ]
        assert len(receipts) == 3
        assert all(
            "Filed " in r and "Drive › Decibyl/" in r and "https://drive.example/" in r
            for r in receipts
        )

    async def test_an_identity_document_cannot_be_filed_without_an_owner(self):
        aadhaar = _document(filename="aadhaar.jpg")
        with patch.object(
            filing.db_client, "get_document_by_uuid", AsyncMock(return_value=aadhaar)
        ):
            result = await filing.file_for_thread(
                ORG, {"document_uuid": aadhaar.document_uuid, "kind": "identity"}
            )
        assert result["status"] == "error"
        assert "owner's name" in result["error"]

    async def test_a_confirmed_holder_files_the_identity_document(self):
        drive, record, merge, reply = _Drive(), AsyncMock(), AsyncMock(), AsyncMock()
        aadhaar = _document(
            filename="aadhaar.jpg",
            mime_type="image/jpeg",
            full_text="Aadhaar",
            custom_metadata={"s3_key": "k", "fields_proposed": {"holder_name": "M"}},
        )
        with ExitStackPatches(_filing_patches(drive, record, merge, reply)):
            result = await filing.on_confirmed(
                ORG, aadhaar, {"holder_name": "Meera Iyer"}
            )
        assert result["status"] == "success"
        assert result["name"] == "Identity - Aadhaar - Meera Iyer - 2026-09-15.jpg"

    async def test_confirming_a_non_identity_document_files_nothing_more(self):
        document = _document(custom_metadata={"s3_key": "k"})
        with patch.object(documents, "_drive", AsyncMock()) as drive:
            result = await filing.on_confirmed(
                ORG, document, {"policy_number": "LIC-77"}
            )
        assert result == {"status": "not_identity"}
        drive.assert_not_awaited()


class ExitStackPatches:
    """``with *patches:`` for a tuple built elsewhere."""

    def __init__(self, patches):
        from contextlib import ExitStack

        self._patches = patches
        self._stack = ExitStack()

    def __enter__(self):
        for p in self._patches:
            self._stack.enter_context(p)
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)


class TestTheTool:
    def test_it_is_offered_to_decibyl(self):
        from api.services.workflow import decibyl

        names = [t["name"] for t in decibyl.office_tools()]
        assert filing.TOOL_NAME in names
        schema = filing.tool_schema()
        assert schema["parameters"]["properties"]["kind"]["enum"] == list(filing.KINDS)
        assert "never a name you read off the document" in schema["description"]
