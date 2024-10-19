import React, { useState } from 'react';

const FetchAndParseURL = () => {
  const [url, setUrl] = useState('');
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const handleFetch = async () => {
    try {
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const result = await response.json();
      setData(result);
    } catch (err) {
      setError(err.message);
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




