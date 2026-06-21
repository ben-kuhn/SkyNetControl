import { apiFetch } from "./client";

export interface VersionInfo {
  version: string;
  git_sha: string;
}

export async function fetchVersion(): Promise<VersionInfo> {
  return apiFetch<VersionInfo>("/version");
}
