/**
 * Typed client for the async-task tracking endpoint.
 *
 * Long-running backend work (ACME issuance, renewal) runs as a Celery task; the
 * mutation returns a `task_id` and the frontend polls {@link tasks.get} until the
 * task is `ready`. Shapes are derived from the generated OpenAPI schema.
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type TaskStatus = Schemas["TaskStatus"];

const BASE = "/api/v1/tasks";

export const tasks = {
  get: (id: string) => api.get<TaskStatus>(`${BASE}/${id}`),
} as const;

export interface PollTaskOptions {
  intervalMs?: number;
  timeoutMs?: number;
  /** Aborts polling early (e.g. component unmount). */
  signal?: AbortSignal;
}

/**
 * Poll a task until it reports `ready`, then resolve with its final status.
 *
 * Resolves with the last-seen status on timeout (still not ready) so callers can
 * surface a "still running" message rather than hang. Rejects if `signal` aborts.
 */
export async function pollTask(
  taskId: string,
  { intervalMs = 2000, timeoutMs = 120_000, signal }: PollTaskOptions = {},
): Promise<TaskStatus> {
  const deadline = Date.now() + timeoutMs;
  let last = await tasks.get(taskId);
  while (!last.ready && Date.now() < deadline) {
    if (signal?.aborted) throw new DOMException("Polling aborted", "AbortError");
    await delay(intervalMs, signal);
    last = await tasks.get(taskId);
  }
  return last;
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Polling aborted", "AbortError"));
      return;
    }
    const id = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(id);
      reject(new DOMException("Polling aborted", "AbortError"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}
