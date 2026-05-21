import type { CheckIn, CallbookResult, Session } from "../types";
import { apiFetch } from "./client";

export async function fetchSessionCheckins(sessionId: number): Promise<CheckIn[]> {
  return apiFetch<CheckIn[]>(`/checkins/session/${sessionId}`);
}

export async function scanMailbox(sessionId: number): Promise<{ imported: number; checkins: CheckIn[] }> {
  return apiFetch(`/checkins/scan/${sessionId}`, { method: "POST" });
}

export async function createManualCheckin(data: {
  session_id: number;
  callsign: string;
  name: string;
  mode: string;
  city?: string;
  county?: string;
  state?: string;
  comments?: string;
}): Promise<CheckIn> {
  return apiFetch<CheckIn>("/checkins/manual", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateCheckin(
  checkinId: number,
  data: Partial<Pick<CheckIn, "name" | "callsign" | "city" | "county" | "state" | "mode" | "comments" | "parse_status">>,
): Promise<CheckIn> {
  return apiFetch<CheckIn>(`/checkins/${checkinId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function approveSession(sessionId: number): Promise<{ session_status: string; members_updated: number }> {
  return apiFetch(`/checkins/approve/${sessionId}`, { method: "POST" });
}

export async function lookupCallsign(callsign: string): Promise<CallbookResult> {
  return apiFetch<CallbookResult>(`/checkins/lookup/${callsign}`);
}

export async function fetchRecentSessions(): Promise<Session[]> {
  return apiFetch<Session[]>("/schedule/sessions");
}
