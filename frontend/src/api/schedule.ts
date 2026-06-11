import type {
  Season,
  SeasonCreate,
  Session,
  SessionCreate,
  SessionUpdate,
} from "../types";
import { apiFetch } from "./client";

export async function fetchSessions(params?: {
  status?: string;
  season_id?: number;
}): Promise<Session[]> {
  const searchParams = new URLSearchParams();
  if (params?.status) searchParams.set("status", params.status);
  if (params?.season_id)
    searchParams.set("season_id", String(params.season_id));

  const query = searchParams.toString();
  return apiFetch<Session[]>(`/schedule/sessions${query ? `?${query}` : ""}`);
}

export async function fetchSeasons(): Promise<Season[]> {
  return apiFetch<Season[]>("/schedule/seasons");
}

export async function createSeason(body: SeasonCreate): Promise<Season> {
  return apiFetch<Season>("/schedule/seasons", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function deleteSeason(seasonId: number): Promise<void> {
  return apiFetch<void>(`/schedule/seasons/${seasonId}`, { method: "DELETE" });
}

export async function createSession(body: SessionCreate): Promise<Session> {
  return apiFetch<Session>("/schedule/sessions", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function updateSession(
  sessionId: number,
  body: SessionUpdate,
): Promise<Session> {
  return apiFetch<Session>(`/schedule/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}
