import React, { useState } from 'react';
import './Chat.css';

const Chat = () => {
  const [messages, setMessages] = useState([]);
  const [newMessage, setNewMessage] = useState('');

  const handleSendMessage = async(e) => {
    e.preventDefault();
    if (newMessage.trim()) {
      setMessages([...messages, "this is a very long answer to no question the quick brown fox sat on the lazy cat"]);
      setNewMessage('');
         // Perform file upload operation
         const formData = new FormData();
         formData.append('query', newMessage);
      const response = await fetch('http://0.0.0.0:8000/api/query', {
        method: 'POST',
        body: formData,
      });
      console.log(response);
      const result = await response.json();
      setResponseMessage(result.message);
      console.log('Item created successfully:', result.item);
      setMessages([...messages, "still doesnt work !!"]);
      setModalIsOpen(false);
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
        <button type="submit">Query</button>
      </form>
    </div>
  );
};

export default Chat;
