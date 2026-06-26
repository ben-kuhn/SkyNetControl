import type { NetMembershipSummary, NetSummary } from "../types";
import { apiFetch } from "./client";

export async function listNets(): Promise<NetMembershipSummary[]> {
  return apiFetch("/nets");
}

export async function getNet(slug: string): Promise<NetSummary> {
  return apiFetch(`/nets/${encodeURIComponent(slug)}`);
}

export async function createNet(body: { slug: string; name: string }): Promise<NetSummary> {
  return apiFetch("/nets", { method: "POST", body: JSON.stringify(body) });
}

export async function patchNet(slug: string, body: { slug?: string; name?: string; is_public?: boolean }): Promise<NetSummary> {
  return apiFetch(`/nets/${encodeURIComponent(slug)}`, { method: "PATCH", body: JSON.stringify(body) });
}

export async function deleteNet(slug: string): Promise<void> {
  return apiFetch(`/nets/${encodeURIComponent(slug)}`, { method: "DELETE" });
}
