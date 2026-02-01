export default function EventCard({ event }) {
  return (
    <div className="card">
      <h3>{event.title}</h3>
      <p className="meta">{event.place} — {event.date}</p>
    </div>
  )
}
