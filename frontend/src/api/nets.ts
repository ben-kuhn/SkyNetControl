import type { NetMembershipSummary, NetRole, NetSummary } from "../types";
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

export async function patchNet(
  slug: string,
  body: { slug?: string; name?: string; is_public?: boolean },
): Promise<NetSummary> {
  return apiFetch(`/nets/${encodeURIComponent(slug)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function deleteNet(slug: string): Promise<void> {
  return apiFetch(`/nets/${encodeURIComponent(slug)}`, { method: "DELETE" });
}

export interface NetMember {
  callsign: string;
  name: string;
  role: NetRole;
}

export async function listNetMembers(slug: string): Promise<NetMember[]> {
  return apiFetch(`/nets/${encodeURIComponent(slug)}/members`);
}

export async function putNetMember(
  slug: string,
  callsign: string,
  role: NetRole,
): Promise<NetMember> {
  return apiFetch(`/nets/${encodeURIComponent(slug)}/members/${encodeURIComponent(callsign)}`, {
    method: "PUT",
    body: JSON.stringify({ role }),
  });
}

export async function deleteNetMember(slug: string, callsign: string): Promise<void> {
  return apiFetch(`/nets/${encodeURIComponent(slug)}/members/${encodeURIComponent(callsign)}`, {
    method: "DELETE",
  });
}

export async function getNetConfig(slug: string): Promise<Record<string, string>> {
  return apiFetch(`/nets/${encodeURIComponent(slug)}/config`);
}

export async function setNetConfigValue(slug: string, key: string, value: string): Promise<void> {
  return apiFetch(`/nets/${encodeURIComponent(slug)}/config/${encodeURIComponent(key)}`, {
    method: "PUT",
    body: JSON.stringify({ value }),
  });
}

export async function setNetConfigBulk(
  slug: string,
  values: Record<string, string>,
): Promise<void> {
  await apiFetch(`/nets/${encodeURIComponent(slug)}/config/bulk`, {
    method: "PUT",
    body: JSON.stringify({ values }),
  });
}
