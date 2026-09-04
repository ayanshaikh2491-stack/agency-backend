"""
Single execution sub-agent for the AI Agency CEO.
LangGraph-compatible version — no CrewAI dependency.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Execution Agent (data-only, no CrewAI)
# ---------------------------------------------------------------------------

EXECUTION_ROLE = "Execution Engineer"
EXECUTION_GOAL = (
    "Take the CEO's task assignment and implement it cleanly. "
    "Write production-grade code, test it, and report results."
)
EXECUTION_BACKSTORY = (
    "You are a sharp execution engineer who turns the CEO's instructions "
    "into working code. You keep things simple, you don't over-engineer, "
    "and you always verify your work before reporting done."
)

# Agent metadata dict (no CrewAI Agent class dependency)
execution_agent = {
    "role": EXECUTION_ROLE,
    "goal": EXECUTION_GOAL,
    "backstory": EXECUTION_BACKSTORY,
    "allow_delegation": False,
    "max_iter": 15,
}

execution_task_template = (
    "Execute the task assigned by the CEO: '{task_description}'.\n\n"
    "1. Understand the requirement first.\n"
    "2. Write clean, working code.\n"
    "3. Test it before reporting done.\n"
    "4. Report what was created, what it does, and results."
)

execution_task = {
    "description_template": execution_task_template,
    "expected_output": (
        "Implementation report with:\n"
        "- What was built\n"
        "- Files modified\n"
        "- Test results (PASS/FAIL)\n"
        "- Any issues encountered"
    ),
}

# ---------------------------------------------------------------------------
# Agent registry
# ---------------------------------------------------------------------------

_AGENT_MAP = {"execution": execution_agent}
_TASK_MAP = {"execution": execution_task}


def get_agents():
    return list(_AGENT_MAP.values())


def get_tasks():
    return list(_TASK_MAP.values())


def get_agent(name: str) -> Optional[dict]:
    return _AGENT_MAP.get(name)


def get_task(name: str) -> Optional[dict]:
    return _TASK_MAP.get(name)


if __name__ == "__main__":
    print(f"Agency Agents: {len(get_agents())} agent(s)")
    for a in get_agents():
        print(f"  • {a['role']}")
