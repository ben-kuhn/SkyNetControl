import { ApiError } from "../types";

export async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const url = `/api${path}`;

  const headers: Record<string, string> = {};
  if (
    options?.method &&
    ["POST", "PATCH", "PUT"].includes(options.method.toUpperCase()) &&
    options.body
  ) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(url, {
    ...options,
    // Defensive: same-origin in production and dev (Vite proxy makes
    // /api/* same-origin too), so cookies are sent automatically. But
    // setting this explicitly avoids surprises if the topology ever
    // changes (subdomain, separate hosting).
    credentials: "include",
    headers: {
      ...headers,
      ...(options?.headers as Record<string, string>),
    },
  });

  if (!response.ok) {
    // HTTP/2 (and many proxies) send an empty reason phrase, so never rely
    // on statusText alone — guarantee a non-empty detail string for the
    // toast/error surfaces downstream.
    let detail = response.statusText || `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body && typeof body.detail === "string" && body.detail.trim()) {
        detail = body.detail;
      }
    } catch {
      // response body wasn't JSON
    }

    if (
      response.status === 401 &&
      path !== "/auth/me" &&
      window.location.pathname !== "/login"
    ) {
      window.location.href = "/login";
    }

    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json();
}
