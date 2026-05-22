import { apiFetch } from "./client";
import type { Member, MemberCheckin } from "../types";

export async function fetchMembers(): Promise<Member[]> {
  return apiFetch<Member[]>("/checkins/members");
}

export async function fetchMemberHistory(callsign: string): Promise<MemberCheckin[]> {
  return apiFetch<MemberCheckin[]>(`/checkins/by-callsign/${encodeURIComponent(callsign)}`);
}
