import Link from 'next/link'

import Header from '../components/Header'
import EventCard from '../components/EventCard'

const sampleEvents = [
  { id: 'wicked', title: 'Wicked — El Musical', place: 'Teatro Real, Madrid', date: '2026-02-12 20:30' },
  { id: 'lesmis', title: 'Los Miserables', place: 'Teatro Nuevo, Barcelona', date: '2026-02-14 19:00' }
]

export default function Home() {
  return (
    <div>
      <Header />
      <main className="container">
        <h1>Próximos musicales</h1>
        <div className="grid">
          {sampleEvents.map(e => (
            <Link key={e.id} href={`/event/${e.id}`}>
              <a>
                <EventCard event={e} />
              </a>
            </Link>
          ))}
        </div>
      </main>
    </div>
  )
}
