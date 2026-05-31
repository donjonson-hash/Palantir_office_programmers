import { useEffect, useState } from 'react';
import { ShieldCheck, AlertCircle } from 'lucide-react';
import { listAgents, type AgentInfo } from '@/lib/api';

export default function Agents() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listAgents()
      .then((d) => setAgents(d.agents))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="p-6 text-sm text-slate-400">Загрузка штата…</div>;
  if (error)
    return (
      <div className="p-6 flex items-center gap-2 text-sm text-red-600">
        <AlertCircle size={16} /> Не удалось загрузить штат: {error}
      </div>
    );

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-[#1A2F3D]">Штат офиса</h2>
        <span className="text-xs text-slate-400">{agents.length} agents</span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {agents.map((a) => (
          <div key={a.name} className="bg-white rounded-xl border border-slate-200 p-5">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-12 h-12 rounded-full bg-gradient-to-br from-[#2E4A62] to-[#5B8FA8] flex items-center justify-center text-white font-bold text-lg">
                {a.name.charAt(0).toUpperCase()}
              </div>
              <div>
                <h3 className="font-semibold text-[#1A2F3D] capitalize">{a.name}</h3>
                <p className="text-xs text-slate-500">{a.role}</p>
              </div>
            </div>

            <div className="flex items-center gap-1.5 mb-2 text-[10px] text-slate-400 uppercase tracking-wider">
              <ShieldCheck size={12} className="text-[#5B8FA8]" />
              Полномочия ({a.allowed_actions.length})
            </div>
            <div className="flex flex-wrap gap-1.5">
              {a.allowed_actions.map((act) => (
                <span key={act} className="text-[11px] font-mono px-2 py-0.5 rounded-md bg-slate-100 text-slate-600">
                  {act}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
