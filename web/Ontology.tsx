import { useEffect, useState } from 'react';
import { Boxes, ShieldAlert, ListTree, Users, AlertCircle } from 'lucide-react';
import { getOntology, type Ontology } from '@/lib/api';

const tierColor: Record<string, string> = {
  AUTO: 'text-slate-500 bg-slate-100',
  LOW: 'text-sky-700 bg-sky-50',
  MEDIUM: 'text-violet-700 bg-violet-50',
  HIGH: 'text-amber-700 bg-amber-50',
  CRITICAL: 'text-red-700 bg-red-50',
};

function Card({ icon: Icon, title, children }: {
  icon: React.ElementType; title: string; children: React.ReactNode;
}) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5">
      <h3 className="text-sm font-semibold text-[#1A2F3D] mb-4 flex items-center gap-2">
        <Icon size={16} className="text-[#5B8FA8]" /> {title}
      </h3>
      {children}
    </div>
  );
}

export default function Ontology() {
  const [onto, setOnto] = useState<Ontology | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getOntology().then(setOnto).catch((e) => setError(e.message));
  }, []);

  if (error)
    return (
      <div className="p-6 flex items-center gap-2 text-sm text-red-600">
        <AlertCircle size={16} /> {error}
      </div>
    );
  if (!onto) return <div className="p-6 text-sm text-slate-400">Загрузка онтологии…</div>;

  return (
    <div className="p-6 space-y-4">
      {/* Тиры риска — политика одобрения */}
      <Card icon={ShieldAlert} title="Тиры риска">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {Object.entries(onto.risk_tiers).map(([tier, cfg]) => (
            <div key={tier} className="p-3 rounded-lg border border-slate-100">
              <div className="flex items-center justify-between mb-1">
                <span className={`text-[11px] font-semibold px-2 py-0.5 rounded ${tierColor[tier] ?? ''}`}>
                  {tier}
                </span>
                <span className={`text-[10px] ${cfg.requires_approval ? 'text-amber-600' : 'text-emerald-600'}`}>
                  {cfg.requires_approval ? 'нужен человек' : 'авто'}
                </span>
              </div>
              <p className="text-xs text-slate-500">{cfg.description}</p>
            </div>
          ))}
        </div>
      </Card>

      {/* Каталог действий — единственный список того, что можно предложить */}
      <Card icon={ListTree} title={`Каталог действий (${onto.action_types.length})`}>
        <div className="flex flex-wrap gap-2">
          {onto.action_types.map((a) => (
            <span key={a.name}
                  className={`inline-flex items-center gap-1.5 text-xs font-mono px-2.5 py-1 rounded-lg ${tierColor[a.tier] ?? 'bg-slate-100 text-slate-600'}`}>
              {a.name}
              <span className="text-[9px] font-sans opacity-70">{a.tier}</span>
            </span>
          ))}
        </div>
      </Card>

      {/* Объекты домена */}
      <Card icon={Boxes} title="Объекты">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
          {onto.object_types.map((o) => (
            <div key={o.name} className="p-3 rounded-lg bg-slate-50">
              <p className="text-sm font-medium text-[#1A2F3D]">{o.name}</p>
              <p className="text-[10px] text-slate-400 mt-0.5">ключ: {o.key}</p>
              <p className="text-[11px] text-slate-500 mt-1">{o.properties.join(', ')}</p>
            </div>
          ))}
        </div>
      </Card>

      {/* Штат и полномочия */}
      <Card icon={Users} title="Штат и полномочия">
        <div className="space-y-2">
          {onto.agents.map((ag) => (
            <div key={ag.name} className="flex items-start gap-3 p-2.5 rounded-lg bg-slate-50">
              <div className="w-8 h-8 rounded-full bg-gradient-to-br from-[#2E4A62] to-[#5B8FA8] flex items-center justify-center text-white text-xs font-bold flex-shrink-0">
                {ag.name.charAt(0).toUpperCase()}
              </div>
              <div className="min-w-0">
                <p className="text-sm font-medium text-[#1A2F3D] capitalize">
                  {ag.name} <span className="text-xs text-slate-400 font-normal">· {ag.role}</span>
                </p>
                <div className="flex flex-wrap gap-1 mt-1">
                  {ag.allowed_actions.map((act) => (
                    <span key={act} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-white text-slate-500">{act}</span>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
