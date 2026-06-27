"""Operational metrics aggregation (graded numbers — lock the cost/cache math and
the tolerance for older traces). Pure dict-in/dict-out; no API."""
from src.observability import aggregate, progress_line


def _trace(case, status, inp, out, cr, cc, calls, secs=None, imgs=1, obj="car", risk="none", err=None):
    t = {
        "case_id": case, "claim_object": obj,
        "agent": {"model": "claude-opus-4-8", "api_calls": calls, "repaired": 0,
                  "usage": {"input_tokens": inp, "output_tokens": out,
                            "cache_read_input_tokens": cr, "cache_creation_input_tokens": cc}},
        "images_processed": imgs, "output": {"claim_status": status, "risk_flags": risk},
    }
    if secs is not None:
        t["wall_clock_seconds"] = secs
    if err:
        t["error"] = err
    return t


def test_cost_and_calls():
    m = aggregate([_trace("c1", "supported", 1_000_000, 0, 0, 0, 2),
                   _trace("c2", "contradicted", 0, 1_000_000, 0, 0, 3)], "claude-opus-4-8")
    assert m["cost_usd"]["input"] == 5.0 and m["cost_usd"]["output"] == 25.0
    assert m["cost_usd"]["total_usd"] == 30.0
    assert m["model_calls"]["total"] == 5
    assert m["claim_status_distribution"] == {"supported": 1, "contradicted": 1}
    assert m["images_processed"] == 2


def test_cache_pct_and_cache_cost():
    m = aggregate([_trace("c1", "supported", 100, 10, 900, 0, 1)], "claude-opus-4-8")
    assert m["cache"]["pct_input_from_cache"] == 90.0          # 900 / (100+900+0)
    assert m["cost_usd"]["cache_read"] == round(900 / 1e6 * 0.5, 4)


def test_tolerates_old_trace_without_api_calls():
    t = {"case_id": "c", "claim_object": "car", "agent": {"rounds": 2, "forced": True, "usage": {}},
         "output": {"claim_status": "supported", "risk_flags": "none"}}
    m = aggregate([t], "claude-opus-4-8")
    assert m["model_calls"]["total"] == 3                       # rounds 2 + forced 1


def test_safe_default_and_mrr_counts():
    m = aggregate([_trace("c1", "not_enough_information", 0, 0, 0, 0, 0, risk="manual_review_required", err="boom")],
                  "claude-opus-4-8")
    assert m["rows_errored"] == 1 and m["rows_safe_default"] == 1
    assert m["manual_review_required_rate"] == 1.0


def test_progress_line_never_crashes_on_sparse_trace():
    s = progress_line(1, 10, {"case_id": "cX", "claim_object": "car", "agent": {"usage": {}}, "output": {}})
    assert "[1/10]" in s and "cX" in s


def test_aggregate_rolls_up_429s_and_error_classes():
    t1 = _trace("c1", "supported", 0, 0, 0, 0, 1)
    t1["agent"]["rate_limit_429s"] = 2
    t2 = _trace("c2", "not_enough_information", 0, 0, 0, 0, 0, err="boom")
    t2["error_class"] = "api_error"
    m = aggregate([t1, t2], "claude-opus-4-8")
    assert m["rate_limit_429s"] == 2
    assert m["error_class_distribution"] == {"api_error": 1}


def test_aggregate_mrr_driver_distribution():
    t = _trace("c1", "supported", 0, 0, 0, 0, 1, risk="manual_review_required;user_history_risk")
    t["decision"] = {"mrr_drivers": ["history", "low_confidence"]}
    m = aggregate([t], "claude-opus-4-8")
    assert m["manual_review_driver_distribution"] == {"history": 1, "low_confidence": 1}


def test_call_model_fails_over_to_fallback():
    import pytest
    import src.agent as agent
    calls = []

    class _Client:
        class messages:
            @staticmethod
            def create(**kw):
                calls.append(kw["model"])
                if kw["model"] == "primary":
                    raise RuntimeError("overloaded")
                return "FALLBACK_RESP"

    trace: dict = {}
    out = agent._call_model(_Client(), trace, fallback_model="backup", model="primary", max_tokens=10)
    assert out == "FALLBACK_RESP" and trace["fallback_model_used"] == "backup"
    assert calls == ["primary", "backup"]
    with pytest.raises(RuntimeError):  # no fallback configured -> propagates
        agent._call_model(_Client(), {}, fallback_model=None, model="primary", max_tokens=10)


def test_classify_error_taxonomy():
    from src.errors import classify_error
    assert classify_error(ValueError("no usable images")) == "no_usable_images"
    assert classify_error("perception finalize failed: ...") == "perception_finalize"
    assert classify_error(TimeoutError("read timed out")) == "timeout"
    assert classify_error("Error 429 rate limit") == "rate_limit"
    assert classify_error(type("ValidationError", (Exception,), {})("bad")) == "validation_fail"
    assert classify_error("PIL cannot identify image file") == "decode_fail"
    assert classify_error("") == "unknown"


def test_capture_provenance_and_429_counter():
    import src.agent as agent

    class _Blk:
        def __init__(self, **kw): self.__dict__.update(kw)

    class _Resp:
        _request_id = "req_abc123"
        content = [_Blk(type="thinking", thinking="weighing the dent"),
                   _Blk(type="text", text="looks like a dent")]

    trace = {}
    agent._capture_provenance(_Resp(), trace)
    assert trace["request_ids"] == ["req_abc123"]
    assert trace["rationale"] == ["weighing the dent looks like a dent"]

    class _RL(Exception):
        status_code = 429

    class _Client:
        class messages:
            @staticmethod
            def create(**kw): raise _RL("rate limited")
    t2: dict = {}
    try:
        agent._call_model(_Client(), t2, model="m")
    except _RL:
        pass
    assert t2["rate_limit_429s"] == 1
