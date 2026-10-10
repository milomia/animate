import React, { useState, FormEvent, ChangeEvent } from 'react';
import './Chat.css';

type ApiResponse = { result?: unknown };

const Chat: React.FC = () => {
  const [messages, setMessages] = useState<string[]>([]);
  const [newMessage, setNewMessage] = useState<string>('');
  const [url, setUrl] = useState<string>('');
  // placeholders removed (unused)

  const handleSendMessage = async (e: FormEvent<HTMLFormElement>): Promise<void> => {
    e.preventDefault();
    let query_url = '';
    if (url.trim()) {
      query_url = 'http://127.0.0.1:8000/api/query_web';
    } else {
      query_url = 'http://127.0.0.1:8000/api/query_llm';
    }
    if (newMessage.trim()) {
      setMessages((prev) => [...prev, newMessage]);
      setNewMessage('');
         // Perform file upload operation
         const formData = new FormData();
         formData.append('query', newMessage);
         formData.append('url', url);
      const response = await fetch(query_url, {
        method: 'POST',
        body: formData,
      });
      console.log(response);
      const result: ApiResponse = await response.json();
      const str = typeof result.result === 'string' ? result.result : JSON.stringify(result.result);
      console.log(str);
      // debugger;
      
      // the code below doesnt work ?
      console.log('Item created successfully:', str);
      setMessages((prev) => [...prev, str]);
      // setModalIsOpen(false);
    }
  };

  return (
    <div className="chat-container">
      <div className="messages">
        {messages.map((msg: string, index: number) => (
          <div key={index} className="message">{msg}</div>
        ))}
      </div>
      <form onSubmit={handleSendMessage} className="message-form">
        <input
          type="text"
          value={newMessage}
          onChange={(e: ChangeEvent<HTMLInputElement>) => setNewMessage(e.target.value)}
          placeholder="Type your message..."
        />
        <input
          type="text"
          value={url}
          onChange={(e: ChangeEvent<HTMLInputElement>) => setUrl(e.target.value)}
          placeholder="Enter URL"
        />
        <button type="submit">Query</button>
      </form>
    </div>
  );
};

export default Chat;
