import { apiFetch } from "./client";
import { ApiError } from "../types";

export interface SmtpConfig {
  host: string;
  port: number;
  username: string;
  password: string; // "***" or "" from server
  from_address: string;
  use_tls: boolean;
}

export interface SmtpUpsert {
  host: string;
  port: number;
  username: string;
  password: string; // "" = preserve; "-" = clear; else set
  from_address: string;
  use_tls: boolean;
}

export interface SmtpTestRequest extends SmtpUpsert {
  to_address: string;
}

export interface SmtpTestResult {
  ok: boolean;
  error?: string;
}

export async function getSmtp(): Promise<SmtpConfig | null> {
  try {
    return await apiFetch<SmtpConfig>("/admin/smtp");
  } catch (e: unknown) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

export function upsertSmtp(body: SmtpUpsert): Promise<SmtpConfig> {
  return apiFetch<SmtpConfig>("/admin/smtp", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function clearSmtp(): Promise<void> {
  return apiFetch<void>("/admin/smtp", { method: "DELETE" });
}

export function sendSmtpTest(body: SmtpTestRequest): Promise<SmtpTestResult> {
  return apiFetch<SmtpTestResult>("/admin/test/smtp", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
