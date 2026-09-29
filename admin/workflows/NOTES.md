# TAGS Agency Workspace Notes

## Agency Overview
- **Purpose**: Automated prospecting, lead qualification, and client delivery.
- **Agents**: CEO, SBA, Content, Website, SEO, Social
- **Channels**: Email, Telegram, Website, Social Media

## Workflow Loops
1. **Prospecting Loop**:
   - **Trigger**: Heartbeat event (every 120s)
   - **Checkpoint**: None (autonomous)
   - **Push Right**: Research, outreach, and content generation
   - **Brief**: Summary of leads found, content created, and website updates

2. **Lead Qualification Loop**:
   - **Trigger**: New lead created
   - **Checkpoint**: SBA review (no external contact)
   - **Push Right**: Internal analysis only
   - **Brief**: Qualification report with score and notes

3. **Client Delivery Loop**:
   - **Trigger**: Lead conversion
   - **Checkpoint**: CEO approval for external actions
   - **Push Right**: Website setup, content creation, and outreach
   - **Brief**: Client onboarding status and next steps

## Tools and Channels
- **Email**: Brevo API
- **Telegram**: Webhook API
- **Website**: Vercel Frontend
- **Database**: SQLite (local), Supabase (cloud)

## Terminology
- **Lead**: Potential client identified for outreach
- **Prospect**: Lead with a qualification score > 70
- **Client**: Prospect with signed agreement
- **Pipeline**: Active leads in various stages

## Workflow Specs
- Each workflow will be defined in `workflows/*.md`
- Workflows are the source of truth for automation

## Implementation Plan
1. **Prospecting Workflow**:
   - **Agents**: CEO, SBA
   - **Tasks**: Identify niches, research leads, generate content
   - **Output**: List of leads with scores and notes

2. **Qualification Workflow**:
   - **Agents**: SBA
   - **Tasks**: Analyze lead data, assign scores, add notes
   - **Output**: Qualification report

3. **Delivery Workflow**:
   - **Agents**: Website, Content, SEO
   - **Tasks**: Set up client website, create content, optimize SEO
   - **Output**: Client dashboard and analytics