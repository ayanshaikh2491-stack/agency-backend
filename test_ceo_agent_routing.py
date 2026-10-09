"""The CEO named no agent, and the turn was wasted.

Recorded decisions show the proposal "..." twice. Both were dropped with "CEO
proposed unknown agent", so on those turns the agency decided nothing and did
nothing. A malformed name is a reason to choose the agent ourselves, not a
reason to skip the work: the task text is still there and is still valid.

The default is sba on purpose. This agency sells to local service businesses, so
a task nobody claimed is far more often unsold than unseoed.
"""
import sys

sys.path.insert(0, ".")

from admin.agency.ceo_autonomy import _agent_for_task, _THINK_AGENTS  # noqa: E402


def test_a_placeholder_proposal_still_reaches_an_agent():
    """The exact failure. The proposal was literally three dots."""
    assert _agent_for_task("...") == "sba"


def test_sales_work_routes_to_the_sales_agent():
    for task in ("find 10 dentists in Pune", "qualify the leads",
                 "write outreach for the clinic", "call the top prospects",
                 "score the pipeline"):
        assert _agent_for_task(task) == "sba", task


def test_search_work_routes_to_seo():
    for task in ("find keywords for dental clinics",
                 "improve the site ranking", "build backlinks"):
        assert _agent_for_task(task) == "seo", task


def test_other_disciplines_route_to_their_own_agent():
    cases = {
        "write a blog post about dental implants": "content",
        "post on instagram and twitter": "social",
        "draft the landing page copy": "website",
        "run a ppc campaign": "ads",
        "report the performance metrics": "analytics",
    }
    for task, expected in cases.items():
        assert _agent_for_task(task) == expected, task


def test_an_empty_task_still_yields_a_real_agent():
    """Never return something outside the allowed set, whatever comes in."""
    for junk in ("", "...", "n/a", "unknown", "ceo2", "???", "None"):
        assert _agent_for_task(junk) in _THINK_AGENTS, junk


def test_every_fallback_is_dispatchable():
    """A fallback the dispatcher would reject would reintroduce the bug."""
    for junk in ("...", "", "whatever", "zzz"):
        assert _agent_for_task(junk) in _THINK_AGENTS


def test_the_allowed_set_covers_every_registered_agent():
    """Nine agents are registered in production. Every one must be reachable."""
    registered = {
        "sba", "content-creator", "seo-engine", "website-builder",
        "ads-runner", "analytics-bot", "social-manager", "memory-agent",
        "analyzing-bot",
    }
    internal = {
        "sba": "sba", "content-creator": "content", "seo-engine": "seo",
        "website-builder": "website", "ads-runner": "ads",
        "analytics-bot": "analytics", "social-manager": "social",
        "memory-agent": "memory", "analyzing-bot": "analyzing",
    }
    allowed = set(_THINK_AGENTS)
    for slug in registered:
        assert internal[slug] in allowed, f"{slug} maps to {internal[slug]}, not dispatchable"