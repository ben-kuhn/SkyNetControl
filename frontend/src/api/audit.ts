import type { AuditEntry } from "../types";
import { apiFetch } from "./client";

export async function fetchAuditLog(
  limit: number = 20,
): Promise<AuditEntry[]> {
  return apiFetch<AuditEntry[]>(`/audit/?limit=${limit}`);
}
