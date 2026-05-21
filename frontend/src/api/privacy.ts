import { apiFetch } from "./client";

export async function exportMyData(): Promise<void> {
  const response = await fetch("/api/privacy/export");
  if (!response.ok) {
    throw new Error("Export failed");
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `skynetcontrol-export.json`;
  a.click();
  URL.revokeObjectURL(url);
}

export async function exportUserData(callsign: string): Promise<void> {
  const response = await fetch(
    `/api/privacy/export/${encodeURIComponent(callsign)}`
  );
  if (!response.ok) {
    throw new Error("Export failed");
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `skynetcontrol-export-${callsign}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

export async function anonymizeMyAccount(): Promise<{
  anonymized: boolean;
  anonymous_id: string;
  message: string;
}> {
  return apiFetch("/privacy/anonymize", {
    method: "POST",
    body: JSON.stringify({ confirm: true }),
  });
}

export async function anonymizeUser(callsign: string): Promise<{
  anonymized: boolean;
  anonymous_id: string;
  message: string;
}> {
  return apiFetch(`/privacy/anonymize/${encodeURIComponent(callsign)}`, {
    method: "POST",
    body: JSON.stringify({ confirm: true }),
  });
}
