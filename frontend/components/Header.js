import { memo } from 'react'

function Header() {
  return (
    <header className="header">
      <div className="logo">BroadWatch</div>
      <nav>
        <a href="/">Inicio</a>
        <a href="/">Calendario</a>
        <a href="/">Novedades</a>
      </nav>
    </header>
  )
}

export default memo(Header)
