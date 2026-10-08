import requests

LLM_BASE_URL = "http://localhost:11434"

# Define the chat input
payload = {
    "model": "deepseek-r1:latest",
    "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is quantum computing?"},
        {"role": "assistant",
         "content": "Quantum computing is a type of computation that uses quantum-mechanical phenomena like superposition and entanglement to process information."},
        {"role": "user", "content": "Can you explain superposition in simple terms?"}
    ],
    "max_tokens": 100,
    "temperature": 0.5
}

# Send the request
response = requests.post(f"{LLM_BASE_URL}/v1/chat/completions", json=payload)

if response.status_code == 200:
    data = response.json()
    print("Chat Response:")
    print(data.get("choices", [{}])[0].get("message", {}).get("content", "No response"))
else:
    print(f"Error: {response.status_code} - {response.text}")
