import { useEffect, useState } from 'react';
import { Bot, ListChecks, ShieldAlert, Zap, HardDrive, AlertCircle } from 'lucide-react';
import {
  listAgents, listTasks, listPending, listAudit,
  type ActionRecord, type TaskRecord,
} from '@/lib/api';

function StatCard({ icon: Icon, label, value, color }: {
  icon: React.ElementType; label: string; value: string | number; color: string;
}) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5 hover:shadow-md transition-shadow">
      <div className={`inline-flex p-2 rounded-lg mb-3 ${color}`}><Icon size={18} /></div>
      <p className="text-2xl font-bold text-[#1A2F3D]">{value}</p>
      <p className="text-sm text-slate-500 mt-1">{label}</p>
    </div>
  );
}

interface Stats {
  agents: number;
  tasks: number;
  pending: number;
  executed: number;
  audit: ActionRecord[];
  recentTasks: TaskRecord[];
}

export default function Dashboard() {
  const [s, setS] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([listAgents(), listTasks(), listPending(), listAudit(50)])
      .then(([a, t, p, au]) =>
        setS({
          agents: a.agents.length,
          tasks: t.tasks.length,
          pending: p.pending.length,
          executed: au.audit.filter((r) => r.status === 'executed').length,
          audit: au.audit.slice(0, 6),
          recentTasks: t.tasks.slice(0, 5),
        }))
      .catch((e) => setError(e.message));
  }, []);

  if (error)
    return (
      <div className="p-6 flex items-center gap-2 text-sm text-red-600">
        <AlertCircle size={16} /> Бэкенд офиса недоступен: {error}
      </div>
    );
  if (!s) return <div className="p-6 text-sm text-slate-400">Загрузка…</div>;

  return (
    <div className="p-6 space-y-6">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={Bot} label="Агентов в штате" value={s.agents} color="bg-blue-50 text-blue-600" />
        <StatCard icon={ListChecks} label="Задач обработано" value={s.tasks} color="bg-emerald-50 text-emerald-600" />
        <StatCard icon={ShieldAlert} label="Ждут одобрения" value={s.pending} color="bg-amber-50 text-amber-600" />
        <StatCard icon={Zap} label="Действий исполнено" value={s.executed} color="bg-violet-50 text-violet-600" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-white rounded-xl border border-slate-200 p-5">
          <h3 className="text-sm font-semibold text-[#1A2F3D] mb-4">Последние действия</h3>
          <div className="space-y-2">
            {s.audit.length === 0 && <p className="text-xs text-slate-400">Пока пусто.</p>}
            {s.audit.map((a) => (
              <div key={a.id} className="flex items-center justify-between text-xs">
                <span className="text-slate-600">
                  <span className="font-medium capitalize">{a.agent_id}</span>
                  <span className="font-mono text-slate-500"> · {a.action}</span> → {a.target}
                </span>
                <span className={a.status === 'executed' ? 'text-emerald-600' : 'text-amber-600'}>
                  {a.status} ({a.tier})
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="bg-white rounded-xl border border-slate-200 p-5">
          <h3 className="text-sm font-semibold text-[#1A2F3D] mb-4 flex items-center gap-2">
            <HardDrive size={16} className="text-[#5B8FA8]" /> Последние задачи
          </h3>
          <div className="space-y-2">
            {s.recentTasks.length === 0 && <p className="text-xs text-slate-400">Пока пусто.</p>}
            {s.recentTasks.map((t) => (
              <div key={t.id} className="flex items-center justify-between text-xs">
                <span className="text-slate-600 truncate mr-2">{t.text}</span>
                <span className="text-slate-400 capitalize whitespace-nowrap">{t.routed_to ?? '—'}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
