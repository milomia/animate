from transformers import AutoModelForCausalLM, AutoTokenizer

# Replace "your-model-name" with the name of your local model or path
model = AutoModelForCausalLM.from_pretrained("/Users/michaelhunt/.ollama/models/manifests/registry.ollama.ai/library/mistral")
tokenizer = AutoTokenizer.from_pretrained("/Users/michaelhunt/.ollama/models/manifests/registry.ollama.ai/library/mistral")

# Example usage:
user_input = "Hello! How can I help you today?"
outputs = model.generate(
    max_new_tokens=500,
    temperature=0.7,
    do_sample=True,
)
response = outputs[0].text
print(f"LLM Response: {response}")

