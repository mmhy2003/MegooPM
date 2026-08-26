/**
 * Error raised for non-2xx responses from the MegooPM backend.
 *
 * Carries the HTTP status and the parsed body (when JSON) so callers and UI
 * layers can branch on `status` (e.g. 401 -> re-auth) or surface `detail`.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }

  /** FastAPI conventionally returns `{ detail: string | ... }` on errors. */
  get detail(): string {
    if (
      this.body &&
      typeof this.body === "object" &&
      "detail" in this.body &&
      typeof (this.body as { detail: unknown }).detail === "string"
    ) {
      return (this.body as { detail: string }).detail;
    }
    return this.message;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }
}
