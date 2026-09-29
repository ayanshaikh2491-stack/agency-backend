import asyncio
import sys
sys.path.append('.')
from admin.comm.telegram import handle_ceo_command, send_telegram_message

async def test():
    response = await handle_ceo_command('/status', [])
    print('Response:', response[:200])
    result = await send_telegram_message(response, '5412605117')
    print('Send result:', result)

asyncio.run(test())