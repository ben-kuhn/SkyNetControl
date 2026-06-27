import type { CheckIn, CallbookResult, Session } from "../types";
import { apiFetch } from "./client";

export async function fetchSessionCheckins(
  sessionId: number,
  netSlug: string,
): Promise<CheckIn[]> {
  return apiFetch<CheckIn[]>(`/nets/${netSlug}/checkins/session/${sessionId}`);
}

export async function scanMailbox(
  sessionId: number,
  netSlug: string,
): Promise<{ imported: number; checkins: CheckIn[] }> {
  return apiFetch(`/nets/${netSlug}/checkins/scan/${sessionId}`, { method: "POST" });
}

export async function createManualCheckin(
  data: {
    session_id: number;
    callsign: string;
    name: string;
    mode: string;
    city?: string;
    county?: string;
    state?: string;
    comments?: string;
  },
  netSlug: string,
): Promise<CheckIn> {
  return apiFetch<CheckIn>(`/nets/${netSlug}/checkins/manual`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateCheckin(
  checkinId: number,
  data: Partial<Pick<CheckIn, "name" | "callsign" | "city" | "county" | "state" | "mode" | "comments" | "parse_status">>,
  netSlug: string,
): Promise<CheckIn> {
  return apiFetch<CheckIn>(`/nets/${netSlug}/checkins/${checkinId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function deleteCheckin(
  checkinId: number,
  netSlug: string,
): Promise<void> {
  await apiFetch<void>(`/nets/${netSlug}/checkins/${checkinId}`, { method: "DELETE" });
}

export async function reparseCheckin(
  checkinId: number,
  netSlug: string,
): Promise<CheckIn> {
  return apiFetch<CheckIn>(`/nets/${netSlug}/checkins/${checkinId}/reparse`, { method: "POST" });
}

export async function reparseSession(
  sessionId: number,
  netSlug: string,
): Promise<{ updated: number; imported: number }> {
  return apiFetch(`/nets/${netSlug}/checkins/session/${sessionId}/reparse`, { method: "POST" });
}

export async function approveSession(
  sessionId: number,
  netSlug: string,
): Promise<{ session_status: string; members_updated: number }> {
  return apiFetch(`/nets/${netSlug}/checkins/approve/${sessionId}`, { method: "POST" });
}

export async function lookupCallsign(
  callsign: string,
  netSlug: string,
): Promise<CallbookResult> {
  return apiFetch<CallbookResult>(`/nets/${netSlug}/checkins/lookup/${callsign}`);
}

export async function fetchRecentSessions(
  netSlug: string,
): Promise<Session[]> {
  return apiFetch<Session[]>(`/nets/${netSlug}/schedule/sessions`);
}

export async function fetchModes(netSlug: string): Promise<string[]> {
  return apiFetch<string[]>(`/nets/${netSlug}/checkins/modes`);
}
