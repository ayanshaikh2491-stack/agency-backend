// ─────────────────────────────────────────────────────────────────────────────
// GENERATED FILE — DO NOT EDIT BY HAND.
// Source of truth: admin/agency/agent_registry.json
// Regenerate:      python -m admin.agency.gen_worker_agents
// Verify (CI):     python -m admin.agency.gen_worker_agents --check
//
// A Cloudflare Worker cannot import the Python registry, so this mirror exists.
// Run the --check command above whenever the registry changes, or the edge
// catalog silently goes stale — which is the drift this file was created to end.
// ─────────────────────────────────────────────────────────────────────────────

export type AgentHost = 'render' | 'cloudflare' | 'both'

export interface AgentEntry {
  id: string
  capabilities: string[]
  host: AgentHost
  /** True when this agent can execute inside the Worker runtime itself. */
  local: boolean
}

export const AGENT_CATALOG: AgentEntry[] = [
  {
    "id": "ceo",
    "capabilities": [
      "chat",
      "strategy",
      "orchestration",
      "approval"
    ],
    "host": "both",
    "local": false
  },
  {
    "id": "sba",
    "capabilities": [
      "lead_gen",
      "qualification",
      "email",
      "handoff",
      "prospecting"
    ],
    "host": "render",
    "local": false
  },
  {
    "id": "seo",
    "capabilities": [
      "keywords",
      "audit",
      "technical",
      "content",
      "backlinks"
    ],
    "host": "both",
    "local": false
  },
  {
    "id": "content",
    "capabilities": [
      "blog",
      "social",
      "hooks",
      "newsletter",
      "copy"
    ],
    "host": "cloudflare",
    "local": true
  },
  {
    "id": "website",
    "capabilities": [
      "design",
      "development",
      "deploy",
      "hosting",
      "maintenance"
    ],
    "host": "render",
    "local": false
  },
  {
    "id": "ads",
    "capabilities": [
      "google",
      "meta",
      "optimization",
      "budget",
      "creative"
    ],
    "host": "cloudflare",
    "local": true
  },
  {
    "id": "social",
    "capabilities": [
      "linkedin",
      "twitter",
      "instagram",
      "scheduling",
      "ads"
    ],
    "host": "cloudflare",
    "local": true
  },
  {
    "id": "analytics",
    "capabilities": [
      "forecast",
      "funnel",
      "dashboard",
      "revenue",
      "metrics"
    ],
    "host": "cloudflare",
    "local": true
  },
  {
    "id": "analyzing",
    "capabilities": [
      "insight",
      "anomaly_detection",
      "root_cause",
      "recommendation"
    ],
    "host": "cloudflare",
    "local": true
  },
  {
    "id": "memory",
    "capabilities": [
      "recall",
      "store",
      "context",
      "summarize"
    ],
    "host": "cloudflare",
    "local": true
  }
]

export const AGENT_IDS: string[] = AGENT_CATALOG.map((a) => a.id)

/** Ids the Worker can run without bouncing the task to the Render host. */
export const LOCAL_AGENT_IDS: string[] = AGENT_CATALOG.filter((a) => a.local).map((a) => a.id)

/** Ids that must be handed to a Render worker (no Chromium/shell available). */
export const RENDER_AGENT_IDS: string[] = AGENT_CATALOG.filter((a) => !a.local).map((a) => a.id)

