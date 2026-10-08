import ollama

response = ollama.chat(model='llama3.2:latest',message=[
{
	'role' : 'user',
	'content' : 'are birds real',
},
])
print(response['message']['content'])
