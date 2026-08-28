import pytest

from api.tasks.knowledge_base_processing import _embed_texts_in_batches


class FakeEmbeddingService:
    def __init__(self):
        self.calls = []

    async def embed_texts(self, texts):
        self.calls.append(list(texts))
        return [[float(len(text))] for text in texts]


@pytest.mark.asyncio
async def test_embed_texts_in_batches_preserves_order():
    service = FakeEmbeddingService()

    embeddings, tokens = await _embed_texts_in_batches(
        service,
        ["a", "bb", "ccc", "dddd", "eeeee"],
        batch_size=2,
    )

    assert service.calls == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]
    assert embeddings == [[1.0], [2.0], [3.0], [4.0], [5.0]]
    # A service reporting no usage contributes nothing rather than raising —
    # a local or self-hosted embedding model has no vendor invoice to read a
    # token count off, and FakeEmbeddingService models exactly that.
    assert tokens == 0


@pytest.mark.asyncio
async def test_embed_texts_in_batches_sums_tokens_across_every_batch():
    """``last_usage_tokens`` is overwritten on each vendor call, so reading it
    once after the loop would bill for the final batch only. Ingestion is the
    one path that always embeds in more than one batch for a real document,
    which is what makes this the expensive way to get it wrong."""

    class CountingService(FakeEmbeddingService):
        def __init__(self):
            super().__init__()
            self.last_usage_tokens = None

        async def embed_texts(self, texts):
            result = await super().embed_texts(texts)
            self.last_usage_tokens = 10 * len(texts)
            return result

    service = CountingService()

    _embeddings, tokens = await _embed_texts_in_batches(
        service,
        ["a", "bb", "ccc", "dddd", "eeeee"],
        batch_size=2,
    )

    # Batches of 2, 2 and 1 -> 20 + 20 + 10, not the 10 of the last batch.
    assert tokens == 50
