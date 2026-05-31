import { useEffect, useState } from 'react';
import { CheckCircle2, XCircle, Clock, UserCheck, Bot, Filter, AlertCircle } from 'lucide-react';
import { listAudit, type ActionRecord } from '@/lib/api';

const statusCfg: Record<string, { color: string; label: string }> = {
  executed: { color: 'text-emerald-500', label: 'Executed' },
  pending: { color: 'text-amber-500', label: 'Pending' },
  rejected: { color: 'text-red-500', label: 'Rejected' },
  rejected_unknown: { color: 'text-red-500', label: 'Unknown' },
  rejected_forbidden: { color: 'text-red-500', label: 'Forbidden' },
};

function statusIcon(s: string) {
  if (s === 'executed') return CheckCircle2;
  if (s === 'pending') return Clock;
  return XCircle;
}

export default function AuditLog() {
  const [rows, setRows] = useState<ActionRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listAudit(100)
      .then((d) => setRows(d.audit))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="p-6 text-sm text-slate-400">Загрузка провенанса…</div>;
  if (error)
    return (
      <div className="p-6 flex items-center gap-2 text-sm text-red-600">
        <AlertCircle size={16} /> {error}
      </div>
    );

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-[#1A2F3D]">Провенанс действий</h2>
        <div className="flex items-center gap-2 text-xs text-slate-400">
          <Filter size={14} /><span>Immutable audit trail</span>
        </div>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <div className="px-5 py-3 bg-slate-50 border-b border-slate-200 text-xs font-medium text-slate-500 uppercase tracking-wider grid grid-cols-12 gap-4">
          <div className="col-span-1">Status</div>
          <div className="col-span-2">Agent</div>
          <div className="col-span-2">Action</div>
          <div className="col-span-3">Target</div>
          <div className="col-span-1">Tier</div>
          <div className="col-span-2">Decided by</div>
          <div className="col-span-1">Time</div>
        </div>

        <div className="divide-y divide-slate-100">
          {rows.map((log) => {
            const cfg = statusCfg[log.status] ?? { color: 'text-slate-400', label: log.status };
            const Icon = statusIcon(log.status);
            const human = log.decided_by && !['auto', 'ontology'].includes(log.decided_by);
            return (
              <div key={log.id} className="grid grid-cols-12 gap-4 px-5 py-3.5 items-center text-sm hover:bg-slate-50/50">
                <div className="col-span-1"><Icon size={16} className={cfg.color} /></div>
                <div className="col-span-2 flex items-center gap-1.5">
                  <Bot size={14} className="text-slate-400" />
                  <span className="text-slate-700">{log.agent_id}</span>
                </div>
                <div className="col-span-2">
                  <span className="text-xs font-mono px-2 py-0.5 rounded bg-slate-100 text-slate-600">{log.action}</span>
                </div>
                <div className="col-span-3 text-slate-700 truncate">{log.target}</div>
                <div className="col-span-1 text-xs text-slate-500">{log.tier}</div>
                <div className="col-span-2">
                  {human ? (
                    <span className="inline-flex items-center gap-1 text-xs text-amber-600">
                      <UserCheck size={12} />{log.decided_by}
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-xs text-emerald-600">
                      <Bot size={12} />{log.decided_by ?? 'auto'}
                    </span>
                  )}
                </div>
                <div className="col-span-1 text-xs text-slate-400">
                  {new Date(log.created_at * 1000).toLocaleTimeString()}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
