# CEO Telegram Integration Workflow

## Overview
- **Purpose**: Enable CEO to interact with the SBA agent via Telegram commands.
- **Agents**: CEO, Telegram Bot
- **Trigger**: Telegram command received
- **Checkpoint**: None (autonomous)
- **Push Right**: Process command and send response
- **Brief**: Telegram response with command details

## Tasks
1. **Receive Telegram Command**:
   - **Agent**: Telegram Bot
   - **Task**: Capture incoming Telegram command.
   - **Output**: Telegram command details.

2. **Process Command**:
   - **Agent**: CEO
   - **Task**: Process the command and generate a response.
   - **Output**: Response for the Telegram bot.

3. **Send Response**:
   - **Agent**: Telegram Bot
   - **Task**: Send the response back to the user.
   - **Output**: Telegram message sent to user.

## Commands
- `/status`: Show agency status
- `/leads`: List recent leads
- `/lead <id>`: Show lead details
- `/approve <id>`: Approve pending action
- `/reject <id>`: Reject pending action
- `/blast <niche>`: Run multi-agent blast for niche
- `/finance`: Show finance snapshot
- `/help`: Show help

## Output
- **Telegram Response**: Command execution status and results.
- **Agency Status**: Current state of the agency.
- **Lead Details**: Information about specific leads.
- **Blast Results**: Results of multi-agent blasts.
- **Finance Snapshot**: Current financial status.

## Integration
- **Telegram Webhook**: Receives commands and sends responses.
- **Agency Database**: Stores and retrieves agency data.
- **Agents**: CEO, SBA, Content, Website, SEO