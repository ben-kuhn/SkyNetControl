import type { Token, TokenCreate, TokenWithSecret } from "../types";
import { apiFetch } from "./client";

export async function createToken(data: TokenCreate): Promise<TokenWithSecret> {
  return apiFetch<TokenWithSecret>("/auth/tokens", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function listTokens(): Promise<Token[]> {
  return apiFetch<Token[]>("/auth/tokens");
}

export async function revokeToken(id: number): Promise<void> {
  await apiFetch<void>(`/auth/tokens/${id}`, { method: "DELETE" });
}
