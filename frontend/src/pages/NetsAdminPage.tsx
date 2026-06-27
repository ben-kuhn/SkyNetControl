import { useEffect, useState } from "react";
import { Button } from "../components/Button";
import { Input } from "../components/Input";
import { Modal } from "../components/Modal";
import { Spinner } from "../components/Spinner";
import { useToast } from "../context/ToastContext";
import {
  createNet,
  deleteNet,
  deleteNetMember,
  listNetMembers,
  listNets,
  patchNet,
  putNetMember,
  type NetMember,
} from "../api/nets";
import type { NetMembershipSummary, NetRole } from "../types";

const ROLE_OPTIONS: NetRole[] = ["viewer", "net_control"];

function MembersPanel({ slug }: { slug: string }) {
  const { addToast } = useToast();
  const [members, setMembers] = useState<NetMember[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [addCallsign, setAddCallsign] = useState("");
  const [addRole, setAddRole] = useState<NetRole>("viewer");
  const [busy, setBusy] = useState(false);

  const load = () => {
    setLoading(true);
    listNetMembers(slug)
      .then((m) => setMembers(m))
      .catch(() => addToast("Failed to load members", "error"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  const handleAdd = async () => {
    if (!addCallsign.trim()) return;
    setBusy(true);
    try {
      await putNetMember(slug, addCallsign.trim().toUpperCase(), addRole);
      addToast("Member added", "success");
      setAddCallsign("");
      load();
    } catch (e) {
      addToast(`Add failed: ${e instanceof Error ? e.message : "unknown"}`, "error");
    } finally {
      setBusy(false);
    }
  };

  const handleRoleChange = async (callsign: string, role: NetRole) => {
    try {
      await putNetMember(slug, callsign, role);
      load();
    } catch {
      addToast("Failed to update role", "error");
    }
  };

  const handleRemove = async (callsign: string) => {
    if (!confirm(`Remove ${callsign} from this net?`)) return;
    try {
      await deleteNetMember(slug, callsign);
      addToast("Member removed", "success");
      load();
    } catch {
      addToast("Failed to remove member", "error");
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-4">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="border-t border-border bg-bg-elevated/40 p-4">
      <div className="mb-3 text-xs font-medium text-text-muted uppercase tracking-wider">
        Members
      </div>
      {members && members.length > 0 ? (
        <table className="w-full text-sm mb-4">
          <thead>
            <tr className="text-left text-text-muted text-xs">
              <th className="font-medium pb-2">Callsign</th>
              <th className="font-medium pb-2">Name</th>
              <th className="font-medium pb-2">Role</th>
              <th className="pb-2"></th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.callsign} className="border-t border-border/40">
                <td className="py-2 font-mono text-text-primary">{m.callsign}</td>
                <td className="py-2 text-text-secondary">{m.name}</td>
                <td className="py-2">
                  <select
                    value={m.role}
                    onChange={(e) => handleRoleChange(m.callsign, e.target.value as NetRole)}
                    className="bg-bg-elevated border border-border rounded-md px-2 py-1 text-sm text-text-primary"
                  >
                    {ROLE_OPTIONS.map((r) => (
                      <option key={r} value={r}>
                        {r.replace("_", " ")}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="py-2 text-right">
                  <Button size="sm" variant="danger" onClick={() => handleRemove(m.callsign)}>
                    Remove
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="text-sm text-text-muted mb-4">No members yet.</div>
      )}

      <div className="flex gap-2 items-end">
        <div className="flex-1">
          <Input
            label="Add member (callsign)"
            value={addCallsign}
            onChange={(e) => setAddCallsign(e.target.value)}
            placeholder="W0NE"
            mono
          />
        </div>
        <select
          value={addRole}
          onChange={(e) => setAddRole(e.target.value as NetRole)}
          className="bg-bg-elevated border border-border rounded-md px-3 py-2 text-sm text-text-primary"
        >
          {ROLE_OPTIONS.map((r) => (
            <option key={r} value={r}>
              {r.replace("_", " ")}
            </option>
          ))}
        </select>
        <Button onClick={handleAdd} loading={busy} disabled={!addCallsign.trim()}>
          Add
        </Button>
      </div>
    </div>
  );
}

function NetRow({ net, onChanged }: { net: NetMembershipSummary; onChanged: () => void }) {
  const { addToast } = useToast();
  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState(net.name);
  const [draftSlug, setDraftSlug] = useState(net.slug);
  const [saving, setSaving] = useState(false);
  const [membersOpen, setMembersOpen] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      const patch: { name?: string; slug?: string } = {};
      if (draftName !== net.name) patch.name = draftName;
      if (draftSlug !== net.slug) patch.slug = draftSlug;
      if (Object.keys(patch).length === 0) {
        setEditing(false);
        return;
      }
      await patchNet(net.slug, patch);
      addToast("Net updated", "success");
      setEditing(false);
      onChanged();
    } catch (e) {
      addToast(`Update failed: ${e instanceof Error ? e.message : "unknown"}`, "error");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (
      !confirm(
        `Delete net "${net.name}" (slug: ${net.slug})?\n\nThis permanently deletes ALL data in this net.`,
      )
    )
      return;
    try {
      await deleteNet(net.slug);
      addToast("Net deleted", "success");
      onChanged();
    } catch (e) {
      addToast(`Delete failed: ${e instanceof Error ? e.message : "unknown"}`, "error");
    }
  };

  return (
    <div className="bg-bg-surface border border-border rounded-lg overflow-hidden mb-4">
      <div className="flex items-center gap-3 p-4">
        {editing ? (
          <>
            <div className="flex-1 grid grid-cols-2 gap-2">
              <Input
                label="Name"
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
              />
              <Input
                label="Slug"
                value={draftSlug}
                onChange={(e) => setDraftSlug(e.target.value)}
                mono
              />
            </div>
            <Button onClick={handleSave} loading={saving}>
              Save
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                setDraftName(net.name);
                setDraftSlug(net.slug);
                setEditing(false);
              }}
            >
              Cancel
            </Button>
          </>
        ) : (
          <>
            <div className="flex-1">
              <div className="font-semibold text-text-primary">{net.name}</div>
              <div className="text-xs text-text-muted font-mono">
                {net.slug}
                {net.is_public && (
                  <span className="ml-2 px-1.5 py-0.5 text-[10px] bg-accent/10 text-accent rounded">
                    public
                  </span>
                )}
              </div>
            </div>
            <Button size="sm" variant="secondary" onClick={() => setMembersOpen((v) => !v)}>
              {membersOpen ? "Hide members" : "Manage members"}
            </Button>
            <Button size="sm" variant="secondary" onClick={() => setEditing(true)}>
              Rename
            </Button>
            <Button size="sm" variant="danger" onClick={handleDelete}>
              Delete
            </Button>
          </>
        )}
      </div>
      {membersOpen && <MembersPanel slug={net.slug} />}
    </div>
  );
}

function CreateNetModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const { addToast } = useToast();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [saving, setSaving] = useState(false);

  const reset = () => {
    setName("");
    setSlug("");
    setSaving(false);
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  const handleCreate = async () => {
    if (!name.trim() || !slug.trim()) return;
    setSaving(true);
    try {
      await createNet({ name: name.trim(), slug: slug.trim() });
      addToast("Net created", "success");
      reset();
      onCreated();
      onClose();
    } catch (e) {
      addToast(`Create failed: ${e instanceof Error ? e.message : "unknown"}`, "error");
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title="Create Net"
      footer={
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={handleClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleCreate} loading={saving} disabled={!name.trim() || !slug.trim()}>
            Create
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-4">
        <Input
          label="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Packet Net"
          autoFocus
        />
        <Input
          label="Slug"
          value={slug}
          onChange={(e) => setSlug(e.target.value)}
          placeholder="packet-net"
          mono
        />
        <p className="text-xs text-text-muted">
          Slug appears in URLs (e.g. <code>/nets/packet-net/schedule</code>). Lowercase letters,
          numbers, and hyphens; must start and end alphanumeric.
        </p>
      </div>
    </Modal>
  );
}

export function NetsAdminPage() {
  const { addToast } = useToast();
  const [nets, setNets] = useState<NetMembershipSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);

  const load = () => {
    setLoading(true);
    listNets()
      .then(setNets)
      .catch(() => addToast("Failed to load nets", "error"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="max-w-3xl">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold text-text-primary">Manage Nets</h1>
        <Button onClick={() => setCreateOpen(true)}>Create Net</Button>
      </div>

      {loading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : nets.length === 0 ? (
        <div className="bg-bg-surface border border-border rounded-lg p-6 text-sm text-text-muted">
          No nets yet. Create one to get started.
        </div>
      ) : (
        nets.map((n) => <NetRow key={n.slug} net={n} onChanged={load} />)
      )}

      <CreateNetModal open={createOpen} onClose={() => setCreateOpen(false)} onCreated={load} />
    </div>
  );
}
