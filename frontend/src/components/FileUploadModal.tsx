import React, { useState, ChangeEvent } from 'react';
import Modal from 'react-modal';
import './FileUploadModal.css';

Modal.setAppElement('#root'); // Set the app element for accessibility

const FileUploadModal: React.FC = () => {
  const [modalIsOpen, setModalIsOpen] = useState<boolean>(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  const openModal = () => setModalIsOpen(true);
  const closeModal = () => setModalIsOpen(false);
  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null;
    setSelectedFile(file);
  };

  const handleUpload = async (): Promise<void> => {
    console.log("handleUpload called");
    if (selectedFile) {
      try {
      // Perform file upload operation
      const formData = new FormData();
      formData.append('file', selectedFile);
      formData.append('filename', selectedFile.name);
       
      const response = await fetch('http://127.0.0.1:8000/api/upload', {
        method: 'POST',
        body: formData,
      });

       if (!response.ok) {
          alert(`Upload failed: ${response.statusText}`);
        return;
    }
      setSelectedFile(null);
      closeModal();
      // alert() blocks rendering, so defer it until the modal has closed
      setTimeout(() => alert('File uploaded successfully'), 100);
  } catch (error) {
    console.error('Upload error:', error);
    alert('Mike Upload failed. Please check if the backend server is running.');
    return;
  }
    } else{
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
