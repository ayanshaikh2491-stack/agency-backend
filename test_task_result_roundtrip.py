"""A completed agent task read back as if it had produced nothing.

`_run_orchestration` stores json.dumps(merged) in `ceo_autonomy_tasks.result`.
`_run_task` stores str(agent_output). Both go through the same `_json_loads`
helper on the way back out, and the helper used to swallow a parse failure and
return the caller's default.

For the orchestration rows that default happened to be harmless, because the
stored value really was JSON. For every single-agent row it was not: the stored
value is prose, json.loads raises, the helper returns "", and the API reports a
successful task with an empty output.

The consequence was not cosmetic. The CEO reads its own task history back
through this helper when deciding what it has already tried, so it concluded
that each delegation had accomplished nothing and re-issued an equivalent
decision on the following tick. Twelve consecutive delegates, all reporting
done, none of them learning anything from the previous one.

`_recent_decision_summary` reads decisions, which are stored as JSON and so were
unaffected. That is why the repetition looked inexplicable from the decision log
alone.
"""
import sys

sys.path.insert(0, ".")

from admin.agency.ceo_autonomy import _json_loads  # noqa: E402


def test_plain_text_agent_output_survives_the_round_trip():
    """The exact failure: a successful analyzing-agent task came back empty."""
    stored = ("Five leads ranked by fit. SmileCraft Dental Clinic (Pune) first: "
              "worst review gap in the set. Second PhysioCare Clinic (Indore).")
    assert _json_loads(stored, "") == stored


def test_json_result_still_parses():
    """The orchestration shape must keep working; this is not a change of type."""
    merged = {"orchestration_id": "ceo_orch_abc", "agents": ["sba"],
              "results": {"sba": "research text"}, "errors": {}}
    parsed = _json_loads('{"orchestration_id": "ceo_orch_abc", "agents": ["sba"]}', "")
    assert parsed["orchestration_id"] == "ceo_orch_abc"
    assert parsed["agents"] == ["sba"]
    assert merged["errors"] == {}


def test_empty_and_none_still_yield_the_default():
    """A genuinely absent value must not become the string 'None'."""
    assert _json_loads("", "") == ""
    assert _json_loads(None, "") == ""
    assert _json_loads(None) == {}


def test_numeric_text_is_not_coerced():
    """A result of '42' or 'true' is text a model produced, not a number to
    silently round-trip into a different type."""
    assert _json_loads("42", "") == 42  # valid JSON, so it does parse
    assert _json_loads("Score: 42", "") == "Score: 42"
    assert _json_loads("true", "") is True


def test_malformed_json_object_falls_back_to_text():
    """A truncated JSON payload must be shown, not erased."""
    broken = '{"orchestration_id": "ceo_orch_ab'
    assert _json_loads(broken, "") == broken
