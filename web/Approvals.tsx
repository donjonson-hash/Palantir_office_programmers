import { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, XCircle, Clock, Shield, AlertCircle } from 'lucide-react';
import { listPending, approve, reject, continueByAction, type ActionRecord } from '@/lib/api';

const tierColor: Record<string, string> = {
  CRITICAL: 'text-red-600 bg-red-50 border-red-200',
  HIGH: 'text-amber-600 bg-amber-50 border-amber-200',
};

export default function Approvals() {
  const [items, setItems] = useState<ActionRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [note, setNote] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    listPending()
      .then((d) => setItems(d.pending))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const decide = async (id: string, kind: 'approve' | 'reject') => {
    if (busy.has(id)) return;
    setBusy((p) => new Set(p).add(id));
    setNote(null);
    try {
      if (kind === 'approve') {
        await approve(id, 'don');
        // Если действие принадлежит многошаговой задаче — продолжить её.
        const { run } = await continueByAction(id);
        if (run) {
          setNote(
            run.status === 'done'
              ? `Задача завершена: ${run.summary ?? 'готово'}`
              : run.status === 'waiting_approval'
                ? 'Задача продолжилась и ждёт одобрения следующего шага.'
                : `Задача: ${run.status}.`,
          );
        }
      } else {
        await reject(id, 'don');
      }
      await refresh();   // подтянуть новый шаг задачи или опустевшую очередь
    } catch (e) {
      alert(`Ошибка: ${e instanceof Error ? e.message : 'unknown'}`);
    } finally {
      setBusy((p) => { const n = new Set(p); n.delete(id); return n; });
    }
  };

  if (loading) return <div className="p-6 text-sm text-slate-400">Загрузка очереди…</div>;
  if (error)
    return (
      <div className="p-6 flex items-center gap-2 text-sm text-red-600">
        <AlertCircle size={16} /> {error}
      </div>
    );

  return (
    <div className="p-6 space-y-6">
      {note && (
        <div className="flex items-center gap-2 px-4 py-3 rounded-xl bg-sky-50 border border-sky-200 text-sm text-sky-800">
          <CheckCircle2 size={16} className="text-sky-600" /> {note}
        </div>
      )}
      {items.length > 0 ? (
        <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-amber-50 border border-amber-200 text-amber-800">
          <Shield size={18} className="text-amber-600 flex-shrink-0" />
          <p className="text-sm">
            <span className="font-semibold">{items.length} действ.</span> ждут одобрения человека перед исполнением.
          </p>
        </div>
      ) : (
        <div className="px-4 py-3 rounded-xl bg-slate-50 border border-slate-200 text-sm text-slate-500">
          Очередь пуста.
        </div>
      )}

      <div className="space-y-3">
        {items.map((req) => {
          const isBusy = busy.has(req.id);
          const tc = tierColor[req.tier] ?? 'text-slate-600 bg-slate-50 border-slate-200';
          return (
            <div key={req.id} className="bg-white rounded-xl border border-amber-200 p-5">
              <div className="flex items-start justify-between">
                <div className="flex items-start gap-4">
                  <div className={`p-2.5 rounded-lg border ${tc}`}>
                    <Clock size={20} />
                  </div>
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <h4 className="text-sm font-semibold text-[#1A2F3D]">
                        {req.action} → {req.target}
                      </h4>
                      <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full border ${tc}`}>
                        {req.tier}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 text-xs text-slate-500">
                      <span>предложил: {req.agent_id}</span>
                      <span className="font-mono text-slate-400">{req.id}</span>
                    </div>
                  </div>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => decide(req.id, 'approve')}
                    disabled={isBusy}
                    className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                      isBusy ? 'bg-gray-200 text-gray-500 cursor-not-allowed'
                             : 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100'}`}
                  >
                    <CheckCircle2 size={14} /> {isBusy ? '…' : 'Approve'}
                  </button>
                  <button
                    onClick={() => decide(req.id, 'reject')}
                    disabled={isBusy}
                    className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                      isBusy ? 'bg-gray-200 text-gray-500 cursor-not-allowed'
                             : 'bg-red-50 text-red-700 hover:bg-red-100'}`}
                  >
                    <XCircle size={14} /> Reject
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
