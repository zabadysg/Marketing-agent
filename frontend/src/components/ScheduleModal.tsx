import { useEffect, useState } from 'react'
import { getConnections, schedulePost } from '../api'
import type { Connection, Post } from '../types'

interface Props {
  post: Post
  wsId: string
  onDone: (updated: Post) => void
  onClose: () => void
}

export default function ScheduleModal({ post, wsId, onDone, onClose }: Props) {
  const [connections, setConnections] = useState<Connection[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedConn, setSelectedConn] = useState('')
  const [when, setWhen] = useState(() => {
    const d = new Date()
    d.setDate(d.getDate() + 1)
    d.setHours(9, 0, 0, 0)
    return d.toISOString().slice(0, 16)
  })
  const [scheduling, setScheduling] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    getConnections(wsId)
      .then(r => {
        setConnections(r.connections.filter(c => !c.disabled))
        if (r.connections.length > 0) setSelectedConn(r.connections[0].id)
      })
      .catch(() => setError('تعذّر تحميل الاتصالات'))
      .finally(() => setLoading(false))
  }, [wsId])

  async function handleSchedule() {
    const conn = connections.find(c => c.id === selectedConn)
    if (!conn) return
    setScheduling(true)
    setError('')
    try {
      const updated = await schedulePost(post.id, conn.id, conn.identifier, new Date(when).toISOString())
      onDone(updated)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'فشلت الجدولة')
      setScheduling(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-md" onClick={e => e.stopPropagation()}>
        <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
          <h3 className="font-semibold text-gray-900">جدولة المنشور</h3>
        </div>
        <div className="p-6 space-y-4">
          {loading ? (
            <div className="text-gray-400 text-sm text-center py-4">جارٍ تحميل الاتصالات…</div>
          ) : connections.length === 0 ? (
            <div className="text-center text-gray-500 text-sm py-4">
              <p>لا توجد قنوات متصلة.</p>
              <p className="text-xs text-gray-400 mt-1">قم بربط حساب اجتماعي في Postiz أولاً.</p>
            </div>
          ) : (
            <>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-2">القناة</label>
                <div className="space-y-2">
                  {connections.map(c => (
                    <label key={c.id} className={`flex items-center gap-3 p-3 border rounded-lg cursor-pointer transition-colors ${selectedConn === c.id ? 'border-indigo-400 bg-indigo-50' : 'border-gray-200 hover:border-gray-300'}`}>
                      <div>
                        <p className="text-sm font-medium text-gray-900">{c.name}</p>
                        <p className="text-xs text-gray-400">@{c.profile} · {c.identifier}</p>
                      </div>
                      {c.picture && <img src={c.picture} alt="" className="w-8 h-8 rounded-full mr-auto" />}
                      <input type="radio" name="conn" value={c.id} checked={selectedConn === c.id} onChange={() => setSelectedConn(c.id)} className="text-indigo-600 mr-auto" />
                    </label>
                  ))}
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">النشر في</label>
                <input
                  type="datetime-local"
                  value={when}
                  onChange={e => setWhen(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>

              {error && <p className="text-red-600 text-sm">{error}</p>}

              <div className="flex gap-2 pt-1">
                <button onClick={onClose} className="px-4 py-2 rounded-lg text-sm text-gray-600 hover:bg-gray-100 transition-colors">إلغاء</button>
                <button
                  onClick={handleSchedule}
                  disabled={scheduling || !selectedConn}
                  className="flex-1 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
                >
                  {scheduling ? 'جارٍ الجدولة…' : 'جدولة'}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
