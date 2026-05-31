import { useEffect, useState } from 'react';
import { Send, Loader2, CheckCircle2, Clock, XCircle, AlertCircle, ArrowRight } from 'lucide-react';
import { submitTask, listTasks, type TaskRecord } from '@/lib/api';

const statusCfg: Record<string, { icon: React.ElementType; color: string; label: string }> = {
  executed: { icon: CheckCircle2, color: 'text-emerald-500', label: 'Executed' },
  pending: { icon: Clock, color: 'text-amber-500', label: 'Ждёт одобрения' },
  forbidden: { icon: XCircle, color: 'text-red-500', label: 'Forbidden' },
  rejected_unknown: { icon: XCircle, color: 'text-red-500', label: 'Unknown action' },
  no_action: { icon: AlertCircle, color: 'text-slate-400', label: 'Нет действия' },
};

export default function Tasks() {
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = () =>
    listTasks().then((d) => setTasks(d.tasks)).catch((e) => setError(e.message));

  useEffect(() => { refresh(); }, []);

  const send = async () => {
    const t = text.trim();
    if (!t || sending) return;
    setSending(true);
    setError(null);
    try {
      await submitTask(t);
      setText('');
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'unknown');
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="p-6 space-y-6">
      {/* Парадная дверь: постановка задачи в офис */}
      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <label className="text-[10px] text-slate-400 uppercase tracking-wider">Новая задача офису</label>
        <div className="flex gap-2 mt-2">
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') send(); }}
            placeholder="напр. «прогони тесты в syndi-vercel» или «выкати на прод»"
            className="flex-1 px-3 py-2 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-1 focus:ring-[#5B8FA8]"
          />
          <button
            onClick={send}
            disabled={sending || !text.trim()}
            className={`inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              sending || !text.trim()
                ? 'bg-slate-200 text-slate-500 cursor-not-allowed'
                : 'bg-[#2E4A62] text-white hover:bg-[#24394d]'}`}
          >
            {sending ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
            Отправить
          </button>
        </div>
        {error && (
          <p className="mt-2 flex items-center gap-1.5 text-xs text-red-600">
            <AlertCircle size={12} /> {error}
          </p>
        )}
      </div>

      {/* Журнал маршрутизации */}
      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <div className="px-5 py-3 bg-slate-50 border-b border-slate-200 text-xs font-medium text-slate-500 uppercase tracking-wider grid grid-cols-12 gap-4">
          <div className="col-span-5">Задача</div>
          <div className="col-span-2">Исполнитель</div>
          <div className="col-span-2">Действие</div>
          <div className="col-span-3">Исход</div>
        </div>
        <div className="divide-y divide-slate-100">
          {tasks.length === 0 && (
            <div className="px-5 py-6 text-sm text-slate-400">Пока ни одной задачи.</div>
          )}
          {tasks.map((t) => {
            const cfg = statusCfg[t.status ?? ''] ?? { icon: AlertCircle, color: 'text-slate-400', label: t.status ?? '—' };
            const Icon = cfg.icon;
            return (
              <div key={t.id} className="grid grid-cols-12 gap-4 px-5 py-3.5 items-center text-sm">
                <div className="col-span-5 text-[#1A2F3D] truncate">{t.text}</div>
                <div className="col-span-2 flex items-center gap-1 text-slate-600 capitalize">
                  <ArrowRight size={12} className="text-slate-300" />{t.routed_to ?? '—'}
                </div>
                <div className="col-span-2">
                  {t.action
                    ? <span className="text-xs font-mono px-2 py-0.5 rounded bg-slate-100 text-slate-600">{t.action}</span>
                    : <span className="text-xs text-slate-400">—</span>}
                </div>
                <div className="col-span-3 flex items-center gap-1.5">
                  <Icon size={14} className={cfg.color} />
                  <span className={`text-xs ${cfg.color}`}>{cfg.label}</span>
                  {t.tier && <span className="text-[10px] text-slate-400">({t.tier})</span>}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
