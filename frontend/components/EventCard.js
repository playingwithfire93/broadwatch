export default function EventCard({ event }) {
  const monitorKey = event.monitor_key || event.id
  const logsUrl = `/api/logs/${monitorKey}/text?n=10`

  return (
    <div className="card">
      <h3>{event.title}</h3>
      <p className="meta">{event.place} — {event.date}</p>
      <div style={{marginTop:8}}>
        <a className="btn btn-ghost" href={logsUrl} target="_blank" rel="noopener noreferrer">Ver logs</a>
      </div>
    </div>
  )
}
