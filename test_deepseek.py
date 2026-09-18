"""Quick connectivity test for the DeepSeek API (api_deepseek1)."""
import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

api_key = os.environ.get("DEEPSEEK_API_KEY")
if not api_key:
    sys.exit("DEEPSEEK_API_KEY not set (check .env)")

client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

response = client.chat.completions.create(
    model="deepseek-chat",
    messages=[
        {"role": "user", "content": "Write a one-line 'Hello, World!' program in Python."}
    ],
)

print(response.choices[0].message.content)
