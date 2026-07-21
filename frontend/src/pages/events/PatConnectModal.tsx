import { useEffect, useState } from "react";
import { abortPatSession, fetchPatConnectOptions, patConnect } from "../../api/events";
import { Button } from "../../components/Button";
import { Input } from "../../components/Input";
import { Modal } from "../../components/Modal";
import { Spinner } from "../../components/Spinner";
import { usePatSession } from "../../hooks/usePatSession";
import type { PatConnectOptions } from "../../types";

interface Props {
  netSlug: string;
  eventId: number | null;
  open: boolean;
  onClose: () => void;
  onSettled: () => Promise<void>;
}

const TERMINAL = new Set(["completed", "failed", "aborted"]);

export function PatConnectModal({ netSlug, eventId, open, onClose, onSettled }: Props) {
  const [options, setOptions] = useState<PatConnectOptions | null>(null);
  const [alias, setAlias] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [mode, setMode] = useState("ardop");
  const [gateway, setGateway] = useState("");
  const [freq, setFreq] = useState("");
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const session = usePatSession(netSlug, sessionId);

  useEffect(() => {
    if (!open) return;
    setSessionId(null); setError(null); setAlias(""); setAdvanced(false);
    fetchPatConnectOptions(netSlug)
      .then((o) => { setOptions(o); if (o.aliases[0]) setAlias(o.aliases[0].name); })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load connect options"));
  }, [open, netSlug]);

  useEffect(() => {
    if (session && TERMINAL.has(session.status)) void onSettled();
  }, [session, onSettled]);

  async function start() {
    setError(null);
    try {
      const input = advanced ? { mode, gateway, freq: freq || undefined } : { alias };
      const { session_id } = await patConnect(netSlug, eventId, input);
      setSessionId(session_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Connect failed");
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="PAT connection" size="lg">
      {error && <p className="text-danger text-sm mb-2">{error}</p>}
      {sessionId == null ? (
        !options ? <Spinner size="md" /> : (
          <div className="flex flex-col gap-3">
            {!advanced ? (
              <label className="text-sm flex flex-col gap-1">
                Connect alias
                <select className="border border-border rounded p-1 bg-bg-elevated"
                  value={alias} onChange={(e) => setAlias(e.target.value)}>
                  {options.aliases.length === 0 && <option value="">(no aliases in PAT)</option>}
                  {options.aliases.map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
                </select>
              </label>
            ) : (
              <div className="flex flex-col gap-2">
                <label className="text-sm flex flex-col gap-1">Mode
                  <select className="border border-border rounded p-1 bg-bg-elevated"
                    value={mode} onChange={(e) => setMode(e.target.value)}>
                    {["telnet", "ardop", "vara", "varafm", "packet", "pactor"].map((m) =>
                      <option key={m} value={m}>{m}</option>)}
                  </select>
                </label>
                <Input label="Gateway callsign" value={gateway}
                  onChange={(e) => setGateway(e.target.value)} placeholder="KE0GW"
                  list="pat-gateways" />
                <datalist id="pat-gateways">
                  {options.gateways.map((g) => <option key={g.callsign} value={g.callsign}>{g.modes} {g.freq}</option>)}
                </datalist>
                <Input label="Frequency (optional)" value={freq}
                  onChange={(e) => setFreq(e.target.value)} placeholder="7100" />
              </div>
            )}
            <button className="text-xs text-accent text-left" onClick={() => setAdvanced((v) => !v)}>
              {advanced ? "← Use a saved alias" : "Advanced: build a connect →"}
            </button>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={onClose}>Cancel</Button>
              <Button onClick={() => void start()}
                disabled={advanced ? !gateway : !alias}>Connect</Button>
            </div>
          </div>
        )
      ) : (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <span className="px-2 py-0.5 rounded text-xs bg-bg-elevated">{session?.status ?? "connecting"}</span>
            <span className="text-sm text-text-muted">{session?.method_label}</span>
          </div>
          <div className="text-xs text-text-secondary">
            sent {session?.sent_count ?? 0} · received {session?.received_count ?? 0}
          </div>
          <div className="max-h-64 overflow-y-auto bg-bg-elevated rounded p-2 font-mono text-xs">
            {(session?.events ?? []).map((e, i) => <div key={i}>{e.text}</div>)}
            {session && session.status === "connecting" && <Spinner size="sm" />}
          </div>
          {session?.error && <p className="text-danger text-sm">{session.error}</p>}
          <div className="flex justify-end gap-2">
            {session && !TERMINAL.has(session.status) && (
              <Button variant="secondary" onClick={() => void abortPatSession(netSlug, sessionId)}>Abort</Button>
            )}
            {session && TERMINAL.has(session.status) && <Button onClick={onClose}>Done</Button>}
          </div>
        </div>
      )}
    </Modal>
  );
}
