import React, { useState } from 'react';
import Modal from 'react-modal';
import './FileUploadModal.css';

Modal.setAppElement('#root'); // Set the app element for accessibility

const FileUploadModal = () => {
  const [modalIsOpen, setModalIsOpen] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);

  const openModal = () => setModalIsOpen(true);
  const closeModal = () => setModalIsOpen(false);
  const handleFileChange = (e) => setSelectedFile(e.target.files[0]);

  const handleUpload = async () => {
    if (selectedFile) {
      // Perform file upload operation
      const formData = new FormData();
      formData.append('file', selectedFile);
      formData.append('filename', selectedFile.name);
       
      const response = await fetch('http://0.0.0.0:8000/api/upload', {
        method: 'POST',
        body: formData,
      });
      setModalIsOpen(false);
    } else {
      alert('Please select a file first');
    }
  };


  return (
    <div className="file-upload-modal-container">
      <button onClick={openModal}>Upload File</button>
      <Modal
        isOpen={modalIsOpen}
        onRequestClose={closeModal}
        contentLabel="File Upload"
        className="modal"
        overlayClassName="overlay"
      >
        <h2>Select a file to upload</h2>
        <input type="file" onChange={handleFileChange} />
        <button onClick={handleUpload}>Upload</button>
        <button onClick={closeModal}>Close</button>
      </Modal>
    </div>
  );
};

export default FileUploadModal;
