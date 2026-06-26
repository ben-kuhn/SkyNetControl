// TODO(Task 13): replace DEFAULT_NET_SLUG with useCurrentNet() hook once
// the CurrentNetContext is available. Task 14 will wire slug into the API calls.
const DEFAULT_NET_SLUG = "default-net";

import { apiFetch } from "./client";
import type { Member, MemberCheckin } from "../types";

export async function fetchMembers(netSlug: string = DEFAULT_NET_SLUG): Promise<Member[]> {
  return apiFetch<Member[]>(`/nets/${netSlug}/checkins/members`);
}

export async function fetchMemberHistory(
  callsign: string,
  netSlug: string = DEFAULT_NET_SLUG,
): Promise<MemberCheckin[]> {
  return apiFetch<MemberCheckin[]>(`/nets/${netSlug}/checkins/by-callsign/${encodeURIComponent(callsign)}`);
}
