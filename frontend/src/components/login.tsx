import React, { useState, FormEvent, ChangeEvent } from 'react';
import Modal from 'react-modal';
import './Login.css';

Modal.setAppElement('#root');

const Login: React.FC = () => {
  const [email, setEmail] = useState<string>('');
  const [password, setPassword] = useState<string>('');
  const [modalIsOpen, setModalIsOpen] = useState<boolean>(false);

  const openModal = () => setModalIsOpen(true);
  const closeModal = () => setModalIsOpen(false);

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();

    // Validate input
    if (!email || !password) {
      alert('Please fill in all fields');
      return;
    }

    try {
      // Call backend authentication endpoint
      const formData = new FormData();
      formData.append('username', email);
      formData.append('password', password);

      const response = await fetch('http://127.0.0.1:8000/api/validate_login', {
        method: 'POST',
        body: formData,
      });

      if (response.ok) {
        const data = await response.json();
        console.log('Login successful:', data);
        // Store the JWT token
        localStorage.setItem('access_token', data.access_token);
        alert('Login successful!');
        closeModal(); // Close modal on successful login
      } else {
        console.error('Login failed:', response.statusText);
        alert('Login failed: Invalid credentials');
      }
    } catch (error: unknown) {
      console.error('Error during login:', error);
      alert('An error occurred during login');
    }
  };

  return (
    <>
      <button onClick={openModal} className="login-trigger-btn">
        Login
      </button>
      
      <Modal
        isOpen={modalIsOpen}
        onRequestClose={closeModal}
        contentLabel="Login"
        className="login-modal"
        overlayClassName="login-modal-overlay"
      >
        <div className="login-container">
          <form onSubmit={handleSubmit}>
            <h2>Ollama Chat Typescript Version</h2>
            <div className="form-group">
              <label>Login (email)</label>
              <input
                type="email"
                value={email}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setEmail(e.target.value)}
                required
              />
            </div>
            <div className="form-group">
              <label>Password:</label>
              <input
                type="password"
                value={password}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setPassword(e.target.value)}
                required
              />
            </div>
            <div className="modal-buttons">
              <button type="button" onClick={closeModal} className="cancel-btn">
                Cancel
              </button>
              <button type="submit" className="submit-btn">
                Login
              </button>
            </div>
          </form>
        </div>
      </Modal>
    </>
  );
};

export default Login;
