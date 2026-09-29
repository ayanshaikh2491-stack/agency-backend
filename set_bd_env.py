import requests
import json

# Try different possible API endpoints
endpoints = [
    "https://api.basicdeploy.com/v1/projects/pzx9k1h2/environment",
    "https://api.basicdeploy.com/v1/projects/pzx9k1h2/env",
    "https://api.basicdeploy.com/projects/pzx9k1h2/environment",
    "https://api.basicdeploy.com/projects/pzx9k1h2/env",
    "https://basicdeploy.com/api/v1/projects/pzx9k1h2/environment",
    "https://basicdeploy.com/api/projects/pzx9k1h2/environment",
]

headers = {
    "Authorization": "Bearer bd_5CxzAJ3svJwWqeDJtbhsKO2njsVOQyGE3xOInjhB",
    "Content-Type": "application/json"
}
data = {
    "TELEGRAM_BOT_TOKEN": "8982126427:AAGaivlbVjGOdicanAVjbgV6mvV_PKwS0eY",
    "TELEGRAM_CHAT_ID": "5412605117",
    "TELEGRAM_WEBHOOK_URL": "https://pzx9k1h2.basicdeploy.com/telegram/webhook"
}

for url in endpoints:
    try:
        print(f"Trying: {url}")
        response = requests.patch(url, headers=headers, json=data, timeout=10)
        print(f"  Status: {response.status_code}")
        print(f"  Response: {response.text[:200]}")
    except Exception as e:
        print(f"  Error: {e}")