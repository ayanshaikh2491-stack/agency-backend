import os
print('TELEGRAM_BOT_TOKEN:', os.getenv('TELEGRAM_BOT_TOKEN', 'NOT SET')[:10] + '...')
print('TELEGRAM_CHAT_ID:', os.getenv('TELEGRAM_CHAT_ID', 'NOT SET'))
print('TELEGRAM_WEBHOOK_URL:', os.getenv('TELEGRAM_WEBHOOK_URL', 'NOT SET'))