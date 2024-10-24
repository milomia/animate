import React, { useState } from 'react';

const FetchAndParseURL = () => {
  const [url, setUrl] = useState('');
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [newMessage, setNewMessage] = useState('what are the headlines?');

  const handleFetch = async(e) => {
    e.preventDefault();
    if (newMessage.trim()) {
      debugger;
      setNewMessage('');
         // Perform file upload operation
         const formData = new FormData();
         // pass the url back
         formData.append('query', url);
      const response = await fetch('http://0.0.0.0:8000/api/query_web', {
        method: 'POST',
        body: formData,
      });
      console.log(response);
      const result = await response.json();
      const str = JSON.stringify(result['result']);
      console.log(str);
      setMessages([...messages, str]);
      // setModalIsOpen(false);
    }
  };

  return (
    <div>
      <input
        type="text"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="Enter URL"
      />
      <button onClick={handleFetch}>Fetch and Parse</button>
      {error && <div style={{ color: 'red' }}>{error}</div>}
      {data && <div><pre>{JSON.stringify(data, null, 2)}</pre></div>}
    </div>
  );
};

export default FetchAndParseURL;




