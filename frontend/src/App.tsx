import React from 'react'
import './App.css'
import Login from './components/login'
import Chat from './components/chat'
import FileUploadModal from './components/FileUploadModal'
// import FetchAndParseURL from './components/web'

const App: React.FC = () => {

  return (
    <>
      <Login/>
      <Chat/>
      <FileUploadModal/>
    </>
  )
}

export default App
