"""
AI Agency — CEO + SBA + Execution Engine Package

Exports the Agency CEO, SBA (Sales/Business Agent), and execution utilities.
Each agent runs inside a LangGraph-based multi-phase thinking architecture.
"""

# NOTE: `AgencyCEO` and `SBAAgent` are imported lazily via __getattr__ below.
# Their modules (ceo.py / sba.py) have heavy top-level import chains
# (LLM clients, admin.config.settings, Supabase checkpointer) that can hang
# on intermittent network access. Importing them eagerly here made EVERY
# `from admin.agency import X` (including lightweight modules like `memory`,
# `mandates`, `workers`) drag in that chain and wedge test/import runs.
# Lazy loading keeps the package cheap to import and only pays the cost when
# a caller actually needs the CEO/SBA classes.

from admin.agency.agency_agents import (
    execution_agent,
    execution_task,
)

_LAZY = {
    "AgencyCEO": ("admin.agency.ceo", "AgencyCEO"),
    "SBAAgent": ("admin.agency.sba", "SBAAgent"),
}


def __getattr__(name: str):
    spec = _LAZY.get(name)
    if spec is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, attr = spec
    import importlib

    module = importlib.import_module(module_path)
    value = getattr(module, attr)
    globals()[name] = value  # cache so future access is cheap
    return value


__all__ = [
    "AgencyCEO",
    "SBAAgent",
    "execution_agent",
    "execution_task",
]
