import React, { useState } from 'react';
import './Chat.css';

const Chat = () => {
  const [messages, setMessages] = useState([]);
  const [newMessage, setNewMessage] = useState('');
  const [url, setUrl] = useState('');
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const handleSendMessage = async(e) => {
    e.preventDefault();
    let query_url=""
    if (url.trim())
    {
      query_url = 'http://0.0.0.0:8000/api/query_web';
    }
    else
    {
      query_url = 'http://0.0.0.0:8000/api/query_llm'
    }
    if (newMessage.trim()) {
      setMessages([...messages, "this is a very long answer to no question the quick brown fox sat on the lazy cat"]);
      setNewMessage('');
         // Perform file upload operation
         const formData = new FormData();
         formData.append('query', newMessage);
         formData.append('url', url);
         debugger;
      const response = await fetch(query_url, {
        method: 'POST',
        body: formData,
      });
      console.log(response);
      const result = await response.json();
      const str = JSON.stringify(result['result']);
      console.log(str);
      // debugger;
      
      // the code below doesnt work ?
      console.log('Item created successfully:', str);
      setMessages([...messages, str]);
      // setModalIsOpen(false);
    }
  };

  return (
    <div className="chat-container">
      <div className="messages">
        {messages.map((msg, index) => (
          <div key={index} className="message">{msg}</div>
        ))}
      </div>
      <form onSubmit={handleSendMessage} className="message-form">
        <input
          type="text"
          value={newMessage}
          onChange={(e) => setNewMessage(e.target.value)}
          placeholder="Type your message..."
        />
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="Enter URL"
        />
        <button type="submit">Query</button>
      </form>
    </div>
  );
};

export default Chat;
