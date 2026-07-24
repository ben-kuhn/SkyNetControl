// frontend/src/pages/events/FormCatalog.tsx
import { useEffect, useState } from "react";
import { fetchFormCatalog } from "../../api/events";
import { Input } from "../../components/Input";
import { Spinner } from "../../components/Spinner";
import type { FormCatalogEntry, FormCatalogNode } from "../../types";

function Node({ node, onPick, depth }: { node: FormCatalogNode; onPick: (e: FormCatalogEntry) => void; depth: number }) {
  return (
    <div style={{ paddingLeft: depth * 12 }}>
      {node.name && <div className="text-xs font-semibold text-text-muted mt-1">{node.name}</div>}
      {node.forms.map((f) => (
        <button key={f.template_path} onClick={() => onPick(f)}
          className="block text-left text-sm text-accent hover:underline py-0.5">
          {f.name}
        </button>
      ))}
      {node.folders.map((sub) => <Node key={sub.name} node={sub} onPick={onPick} depth={depth + 1} />)}
    </div>
  );
}

export function FormCatalog({ eventId, onPick }: { eventId: number; onPick: (e: FormCatalogEntry) => void }) {
  const [tree, setTree] = useState<FormCatalogNode | null>(null);
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchFormCatalog(eventId, q).then(setTree).catch((e) => setError(e instanceof Error ? e.message : "Failed"));
  }, [eventId, q]);

  if (error) return <p className="text-danger text-sm">{error}</p>;
  if (!tree) return <Spinner size="md" />;
  const empty = tree.forms.length === 0 && tree.folders.length === 0;
  return (
    <div className="flex flex-col gap-2">
      <Input label="Search forms" value={q} onChange={(e) => setQ(e.target.value)} placeholder="ICS213" />
      <div className="max-h-96 overflow-y-auto">
        {empty ? <p className="text-text-muted text-sm">No forms — fetch the forms library in config.</p>
               : <Node node={tree} onPick={onPick} depth={0} />}
      </div>
    </div>
  );
}
