"""Translate or transliterate a piece of text for the screen.

Behind the "Translate" action on a message and the offers under the
composer -- see services/translation.py for where it is offered and why it
is not a tool.
"""

from __future__ import annotations

from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db.models import UserModel
from api.services import translation
from api.services.auth.depends import get_user

router = APIRouter(prefix="/translate", tags=["translate"])


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=translation.MAX_INPUT_CHARS)
    #: Where to: a Sarvam language code. English by default.
    target_language_code: str = Field(default=translation.ENGLISH, max_length=8)
    #: "translate" changes the words; "transliterate" keeps them and changes
    #: the script (Hindi typed in Devanagari, read back in Roman letters).
    mode: str = Field(default="translate", max_length=16)


class TranslateResponse(BaseModel):
    text: str
    source_language_code: Optional[str] = None
    target_language_code: str
    mode: str


@router.post("", response_model=TranslateResponse)
async def translate_text(
    body: TranslateRequest, user: UserModel = Depends(get_user)
) -> TranslateResponse:
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    mode = body.mode.strip().lower()
    if mode not in ("translate", "transliterate"):
        raise HTTPException(status_code=422, detail="translate or transliterate")
    try:
        if mode == "translate":
            text, source = await translation.translate(
                body.text, target=body.target_language_code
            )
        else:
            text, source = await translation.transliterate(
                body.text, target=body.target_language_code
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except translation.TranslationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Translation failed: {exc}"
        ) from exc
    return TranslateResponse(
        text=text,
        source_language_code=source,
        target_language_code=body.target_language_code,
        mode=mode,
    )
