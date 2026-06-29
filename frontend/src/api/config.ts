import { apiFetch } from "./client";

export async function fetchConfig(): Promise<Record<string, string>> {
  return apiFetch<Record<string, string>>("/config/");
}

export async function setConfigValue(
  key: string,
  value: string,
): Promise<void> {
  await apiFetch<unknown>(`/config/${encodeURIComponent(key)}`, {
    method: "PUT",
    body: JSON.stringify({ value }),
  });
}

export interface GroupsIoTestResult {
  ok: boolean;
  error?: string;
}

export async function sendGroupsIoTest(): Promise<GroupsIoTestResult> {
  return apiFetch<GroupsIoTestResult>("/admin/test/groupsio", { method: "POST" });
}
