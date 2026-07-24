import { apiFetch } from "./client";

export async function fetchEventConfig(eventId: number): Promise<Record<string, string>> {
  return apiFetch<Record<string, string>>(`/events/${eventId}/config`);
}

export async function saveEventConfig(eventId: number, values: Record<string, string>): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/events/${eventId}/config/bulk`, {
    method: "PUT",
    body: JSON.stringify({ values }),
  });
}
