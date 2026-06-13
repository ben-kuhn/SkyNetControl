import { apiFetch } from "./client";

export interface OAuthProvider {
  slug: string;
  name: string;
  enabled: boolean;
  client_id: string;
  client_secret: string; // always "***" or "" from server
  issuer_url: string;
}

export interface OAuthProviderUpsert {
  name: string;
  enabled: boolean;
  client_id: string;
  client_secret: string; // "" = preserve; "-" = clear; else set
  issuer_url: string;
}

export interface SlugDeriveResult {
  slug: string;
  valid: boolean;
  error?: string;
}

export interface OAuthTestStartResponse {
  test_session_id: string;
  authorize_url: string;
}

export interface OAuthTestResult {
  status: "pending" | "success" | "failed";
  error?: string;
  identity?: Record<string, string>;
}

export function listOAuthProviders(): Promise<OAuthProvider[]> {
  return apiFetch<OAuthProvider[]>("/admin/oauth/providers");
}

export function getOAuthProvider(slug: string): Promise<OAuthProvider> {
  return apiFetch<OAuthProvider>(`/admin/oauth/providers/${encodeURIComponent(slug)}`);
}

export function upsertOAuthProvider(slug: string, body: OAuthProviderUpsert): Promise<OAuthProvider> {
  return apiFetch<OAuthProvider>(`/admin/oauth/providers/${encodeURIComponent(slug)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteOAuthProvider(slug: string): Promise<void> {
  return apiFetch<void>(`/admin/oauth/providers/${encodeURIComponent(slug)}`, {
    method: "DELETE",
  });
}

export function deriveSlug(name: string): Promise<SlugDeriveResult> {
  return apiFetch<SlugDeriveResult>(
    `/admin/oauth/providers/slug/derive?name=${encodeURIComponent(name)}`,
    { method: "POST" },
  );
}

export function startOAuthTest(
  slug: string,
  body: { client_id: string; client_secret: string; issuer_url: string; name: string },
): Promise<OAuthTestStartResponse> {
  return apiFetch<OAuthTestStartResponse>(`/admin/test/oauth/${encodeURIComponent(slug)}/start`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getOAuthTestResult(testSessionId: string): Promise<OAuthTestResult> {
  return apiFetch<OAuthTestResult>(`/admin/test/oauth/${encodeURIComponent(testSessionId)}/result`);
}
