// TODO(Task 13): replace DEFAULT_NET_SLUG with useCurrentNet() hook once
// the CurrentNetContext is available. Task 14 will wire slug into the API calls.
const DEFAULT_NET_SLUG = "default-net";

import type {
  Season,
  SeasonCreate,
  Session,
  SessionCreate,
  SessionUpdate,
} from "../types";
import { apiFetch } from "./client";

export async function fetchSessions(
  netSlug: string = DEFAULT_NET_SLUG,
  params?: {
    status?: string;
    season_id?: number;
  },
): Promise<Session[]> {
  const searchParams = new URLSearchParams();
  if (params?.status) searchParams.set("status", params.status);
  if (params?.season_id)
    searchParams.set("season_id", String(params.season_id));

  const query = searchParams.toString();
  return apiFetch<Session[]>(
    `/nets/${netSlug}/schedule/sessions${query ? `?${query}` : ""}`,
  );
}

export async function fetchSeasons(
  netSlug: string = DEFAULT_NET_SLUG,
): Promise<Season[]> {
  return apiFetch<Season[]>(`/nets/${netSlug}/schedule/seasons`);
}

export async function createSeason(
  body: SeasonCreate,
  netSlug: string = DEFAULT_NET_SLUG,
): Promise<Season> {
  return apiFetch<Season>(`/nets/${netSlug}/schedule/seasons`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function deleteSeason(
  seasonId: number,
  netSlug: string = DEFAULT_NET_SLUG,
): Promise<void> {
  return apiFetch<void>(`/nets/${netSlug}/schedule/seasons/${seasonId}`, {
    method: "DELETE",
  });
}

export async function createSession(
  body: SessionCreate,
  netSlug: string = DEFAULT_NET_SLUG,
): Promise<Session> {
  return apiFetch<Session>(`/nets/${netSlug}/schedule/sessions`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function updateSession(
  sessionId: number,
  body: SessionUpdate,
  netSlug: string = DEFAULT_NET_SLUG,
): Promise<Session> {
  return apiFetch<Session>(`/nets/${netSlug}/schedule/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}
