import requests
import json

url = "http://localhost:9002/telegram/webhook"
data = {
    "message": {
        "chat": {"id": "1234567890"},
        "text": "/status"
    }
}
headers = {"Content-Type": "application/json"}

response = requests.post(url, json=data, headers=headers)
print("Status:", response.status_code)
print("Response:", response.text)