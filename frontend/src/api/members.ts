import { apiFetch } from "./client";
import type { Member, MemberCheckin } from "../types";

export async function fetchMembers(netSlug: string): Promise<Member[]> {
  return apiFetch<Member[]>(`/nets/${netSlug}/checkins/members`);
}

export async function fetchMemberHistory(
  callsign: string,
  netSlug: string,
): Promise<MemberCheckin[]> {
  return apiFetch<MemberCheckin[]>(`/nets/${netSlug}/checkins/by-callsign/${encodeURIComponent(callsign)}`);
}
