import { Hono } from 'hono'
import { cors } from 'hono/cors'
import { createClient } from '@libsql/client'

const app = new Hono()
app.use('*', cors())

// Create DB client per request
function getDB(env: any) {
  return createClient({
    url: env.TURSO_DATABASE_URL,
    authToken: env.TURSO_AUTH_TOKEN,
  })
}

// 1. Telegram webhook - INSTANT response
app.post('/telegram/webhook', async (c) => {
  const body = await c.req.json()
  const db = getDB(c.env)
  
  // Store event for Render workers to process
  await db.execute({
    sql: `INSERT INTO ceo_autonomy_events (event_type, payload, status, created_at) 
          VALUES ('telegram_webhook', ?, 'pending', datetime('now'))`,
    args: [JSON.stringify(body)]
  })
  
  return c.json({ ok: true })
})

// 2. Health check - INSTANT
app.get('/api/health', (c) => c.json({ 
  status: 'ok', 
  timestamp: new Date().toISOString(),
  workers: 'live'
}))

// 3. Status - read from DB (fast)
app.get('/api/status', async (c) => {
  const db = getDB(c.env)
  const leads = await db.execute(
    `SELECT status, COUNT(*) as count FROM leads GROUP BY status`
  )
  return c.json({ success: true, pipeline: leads.rows })
})

// 4. CEO chat - queue message, return conversation_id
app.post('/api/ceo/chat', async (c) => {
  const { message, conversation_id } = await c.req.json()
  const convId = conversation_id || crypto.randomUUID()
  const db = getDB(c.env)
  
  await db.execute({
    sql: `INSERT INTO ceo_conversations (id, user_message, status, created_at)
          VALUES (?, ?, 'queued', datetime('now'))`,
    args: [convId, message]
  })
  
  return c.json({ conversation_id: convId, status: 'queued' })
})

// 5. Poll for CEO response
app.get('/api/ceo/chat/:id', async (c) => {
  const id = c.req.param('id')
  const db = getDB(c.env)
  const result = await db.execute({
    sql: `SELECT response, status FROM ceo_conversations WHERE id = ?`,
    args: [id]
  })
  return c.json(result.rows[0] || { status: 'not_found' }})
})

// 6. Agent task API - queue tasks for Render workers
app.post('/api/agent/:agent_type/run', async (c) => {
  const agentType = c.req.param('agent_type')
  const { workspace_id = "ws_agency", task, safe_only = true } = await c.req.json()
  const db = getDB(c.env)
  
  // Queue task for Render worker
  const taskId = crypto.randomUUID()
  await db.execute({
    sql: `INSERT INTO agent_tasks (id, workspace_id, agent_type, task, safe_only, status, created_at)
          VALUES (?, ?, ?, ?, ?, 'queued', datetime('now'))`,
    args: [taskId, workspace_id, agentType, task, safe_only ? 1 : 0]
  })
  return c.json({ task_id: taskId, status: 'queued' })
})

// 7. Get agent task result
app.get('/api/agent/task/:id', async (c) => {
  const id = c.req.param('id')
  const db = getDB(c.env)
  const result = await db.execute({
    sql: `SELECT result, status, error FROM agent_tasks WHERE id = ?`,
    args: [id]
  })
  return c.json(result.rows[0] || { status: 'not_found' })
})

// 8. List all agents and capabilities
app.get('/api/agents', (c) => c.json({
  agents: [
    { id: "ceo", capabilities: ["chat", "strategy", "orchestration", "approval"] },
    { id: "sba", capabilities: ["lead_gen", "qualification", "email", "handoff", "prospecting"] },
    { id: "seo", capabilities: ["keywords", "audit", "technical", "content", "backlinks"] },
    { id: "content", capabilities: ["blog", "social", "hooks", "newsletter", "copy"] },
    { id: "website", capabilities: ["design", "development", "deploy", "hosting", "maintenance"] },
    { id: "social", capabilities: ["linkedin", "twitter", "instagram", "scheduling", "ads"] },
    { id: "ads", capabilities: ["google", "meta", "optimization", "budget", "creative"] },
    { id: "analytics", capabilities: ["forecast", "funnel", "dashboard", "revenue", "metrics"] }
  ]
}))

// 9. Workspace-specific agent status
app.get('/api/workspaces/:ws/agents', async (c) => {
  const ws = c.req.param('ws')
  const db = getDB(c.env)
  const tasks = await db.execute({
    sql: `SELECT agent_type, status, COUNT(*) as count FROM agent_tasks 
          WHERE workspace_id = ? GROUP BY agent_type, status`,
    args: [ws]
  })
  return c.json({ workspace: ws, agents: tasks.rows })
})

export default app