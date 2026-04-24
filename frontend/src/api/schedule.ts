import type { Session } from "../types";
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
