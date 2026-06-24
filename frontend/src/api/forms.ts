import { apiFetch } from "./client";

export interface FormsStatus {
  source_url: string;
  library_version: string | null;
  last_fetched_at: string | null;
}

export interface FormsFetchResult {
  library_version: string;
  last_fetched_at: string;
}

export async function getFormsStatus(): Promise<FormsStatus> {
  return apiFetch("/config/forms/status");
}

export async function fetchFormsLibrary(): Promise<FormsFetchResult> {
  return apiFetch("/config/forms/fetch", { method: "POST" });
}
