import { apiFetch } from "./client";

export async function retryDelivery(
  contentType: string,
  contentId: number,
  netSlug: string,
): Promise<{ retried: boolean }> {
  return apiFetch<{ retried: boolean }>(
    `/nets/${netSlug}/delivery/${contentType}/${contentId}/retry`,
    { method: "POST" },
  );
}
