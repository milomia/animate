import { useState } from 'react'
import reactLogo from './assets/react.svg'
import viteLogo from '/vite.svg'
import './App.css'
import Login from './components/login'
import Chat from './components/chat'
import FileUploadModal from './components/FileUploadModal'
import FetchAndParseURL from './components/web'

function App() {
  const [count, setCount] = useState(0)

  return (
    <>
      <Login/>
      <Chat/>
      <FileUploadModal/>
      <FetchAndParseURL/>
    </>
  )
}

export default App
