"""One verdict per call, whichever shape QA wrote."""

from api.services.review import verdict


def test_a_per_node_result_becomes_one_line():
    v = verdict.from_annotations(
        {
            "qa_1": {
                "node_results": {
                    "greeting": {
                        "summary": "Caller wanted a refund; agent explained the policy.",
                        "score": 8,
                        "tags": ["refund", {"tag": "policy"}],
                        "overall_sentiment": "neutral",
                    }
                }
            },
            "tags": ["refund"],
        }
    )
    assert v is not None
    assert v.summary.startswith("Caller wanted")
    assert (v.score, v.sentiment, v.tags) == (8, "neutral", ("refund", "policy"))
    assert v.needs_attention is False


def test_a_low_score_or_a_negative_caller_needs_attention():
    low = verdict.from_annotations(
        {"qa": {"node_results": {"a": {"summary": "x", "score": 3}}}}
    )
    angry = verdict.from_annotations(
        {
            "qa": {
                "node_results": {
                    "a": {"summary": "x", "score": 9, "overall_sentiment": "Negative"}
                }
            }
        }
    )
    assert low.needs_attention is True
    assert angry.needs_attention is True


def test_nothing_to_say_is_none():
    assert verdict.from_annotations(None) is None
    assert verdict.from_annotations({"disposition": "answered"}) is None
    assert verdict.from_annotations({"qa": {"node_results": {}}}) is None
