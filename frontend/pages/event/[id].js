import { useRouter } from 'next/router'
import Header from '../../components/Header'

export default function EventPage() {
  const router = useRouter()
  const { id } = router.query

  // Placeholder data; later will fetch from API
  return (
    <div>
      <Header />
      <main className="container">
        <h1>Ficha del musical: {id}</h1>
        <p>Sinopsis corta, reparto, calendario de funciones y enlaces de compra.</p>
      </main>
    </div>
  )
}
