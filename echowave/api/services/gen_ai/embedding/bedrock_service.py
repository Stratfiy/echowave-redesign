"""Knowledge search on Amazon Bedrock embeddings (stream aws-gateway).

Reached only through the managed ``aws`` embeddings tier, which managed
resolution hands over with the gateway's Bedrock marker as its key: the call
is signed with the platform's instance role, so it must never be reachable
from a configuration a customer typed (``factory.build_embedding_service``
refuses any other key).

The vector column holds 1536 numbers, so the model must return that many;
``aws_gateway.config.embeddings_status`` only offers one that can. Chunks are
stored under this model's id, and ``knowledge_base.staleness`` already treats
documents embedded by another model as needing to be read again.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from api import constants
from api.db.db_client import DBClient
from api.services.aws_gateway import bedrock

from .base import BaseEmbeddingService


class BedrockEmbeddingService(BaseEmbeddingService):
    def __init__(self, db_client: DBClient, model_id: str):
        self.db = db_client
        self.model_id = model_id
        #: Tokens Bedrock billed for the last call, when the reply said.
        self.last_usage_tokens: int | None = None

    def get_model_id(self) -> str:
        return self.model_id

    def get_embedding_dimension(self) -> int:
        return constants.BEDROCK_EMBEDDING_DIMENSIONS

    async def _embed(self, texts: List[str], input_type: str) -> List[List[float]]:
        vectors, billed = await bedrock.embed(
            self.model_id, texts, input_type=input_type
        )
        self.last_usage_tokens = billed
        return vectors

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return await self._embed(texts, "search_document")

    async def embed_query(self, query: str) -> List[float]:
        return (await self._embed([query], "search_query"))[0]

    async def search_similar_chunks(
        self,
        query: str,
        organization_id: int,
        limit: int = 5,
        document_uuids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        query_embedding = await self.embed_query(query)
        return await self.db.search_similar_chunks(
            query_embedding=query_embedding,
            organization_id=organization_id,
            limit=limit,
            document_uuids=document_uuids,
            embedding_model=self.model_id,
        )
