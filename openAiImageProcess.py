import base64
import requests

# OpenAI API Key
api_key = "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"

# Function to encode the image
def encode_image(image_path):
  with open(image_path, "rb") as image_file:
    return base64.b64encode(image_file.read()).decode('utf-8')

# Path to your image
image_path = "/Users/andersjuengst/Desktop/bracket.png"

# Getting the base64 string
base64_image = encode_image(image_path)

headers = {
  "Content-Type": "application/json",
  "Authorization": f"Bearer {api_key}"
}

payload = {
  "model": "gpt-4-turbo",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "Attached is a picture of a tournament bracket. Output a json list of each game with the teams, their scores, and the round number. Don't include newline characters or whitespace in the output"
        },
        {
          "type": "image_url",
          "image_url": {
            "url": f"data:image/jpeg;base64,{base64_image}",
          }
        }
      ]
    }
  ],
  "max_tokens": 800
}

response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)

print(response.json())