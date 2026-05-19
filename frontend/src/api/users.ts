import type { User, UserRole } from "../types";
import { apiFetch } from "./client";

export async function fetchUsers(): Promise<User[]> {
  return apiFetch<User[]>("/auth/users");
}

export async function updateUserRole(
  callsign: string,
  role: UserRole,
): Promise<void> {
  await apiFetch<unknown>(`/auth/users/${encodeURIComponent(callsign)}`, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  });
}

export async function approveCallsign(callsign: string): Promise<void> {
  await apiFetch<unknown>(
    `/auth/users/${encodeURIComponent(callsign)}/approve-callsign`,
    { method: "POST" },
  );
}

export async function rejectCallsign(callsign: string): Promise<void> {
  await apiFetch<unknown>(
    `/auth/users/${encodeURIComponent(callsign)}/pending-callsign`,
    { method: "DELETE" },
  );
}
