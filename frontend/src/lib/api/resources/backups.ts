/**
 * Typed client for the backups API (admin-only).
 *
 * Two calls do not go through `api`: downloading needs the raw bytes and
 * uploading needs multipart, and the shared client serialises JSON. Both reach
 * for the same bearer token the rest of the app uses.
 */
import { api } from "@/lib/api/client";
import { getAccessToken } from "@/lib/auth/session";
import type { Schemas } from "@/lib/api/types";

export type Backup = Schemas["BackupRead"];
export type BackupList = Schemas["BackupList"];
export type RestoreStatus = Schemas["RestoreStatus"];

const BASE = "/api/v1/backups";

function url(path: string): string {
  return `${process.env.NEXT_PUBLIC_API_BASE_URL ?? ""}${path}`;
}

export const backups = {
  list: () => api.get<BackupList>(BASE),
  create: () => api.post<Schemas["TaskEnqueued"]>(BASE, {}),
  remove: (name: string) => api.delete<void>(`${BASE}/${encodeURIComponent(name)}`),
  restore: (name: string) =>
    api.post<Schemas["TaskEnqueued"]>(`${BASE}/${encodeURIComponent(name)}/restore`, {}),
  restoreStatus: () => api.get<RestoreStatus>(`${BASE}/restore-status`),

  /** Fetches the bytes and hands them to the browser as a save dialog. */
  async download(name: string): Promise<void> {
    const response = await fetch(url(`${BASE}/${encodeURIComponent(name)}/download`), {
      headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` },
    });
    if (!response.ok) throw new Error(`Download failed with ${response.status}`);
    const href = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = href;
    link.download = name;
    link.click();
    URL.revokeObjectURL(href);
  },

  async upload(file: File): Promise<Backup> {
    const body = new FormData();
    body.append("file", file);
    const response = await fetch(url(`${BASE}/upload`), {
      method: "POST",
      headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` },
      body,
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      throw new Error(detail?.detail ?? `Upload failed with ${response.status}`);
    }
    return (await response.json()) as Backup;
  },
} as const;
