import { invoke, isTauri } from "@tauri-apps/api/core";

/**
 * HTTP transport for the platform. In the desktop app every request goes
 * through the Rust `http_request` command, which avoids webview CORS limits
 * and applies the backend connection's private CA. Outside Tauri (unit tests,
 * plain Vite) it falls back to `fetch`.
 */

export interface HttpRequest {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  url: string;
  headers?: Record<string, string>;
  /** Objects are sent as JSON; strings are sent as-is. */
  body?: unknown;
  timeoutMs?: number;
  /** Apply the backend connection's TLS settings. Use for Proxigo server calls. */
  backendTls?: boolean;
}

export interface HttpResponse {
  status: number;
  ok: boolean;
  url: string;
  contentType: string | null;
  body: string;
}

export type ApiErrorKind = "network" | "http" | "parse" | "config";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly kind: ApiErrorKind,
    readonly status?: number,
    readonly url?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function encodeBody(body: unknown, headers: Record<string, string>): string | undefined {
  if (body === undefined) return undefined;
  if (typeof body === "string") return body;
  if (!Object.keys(headers).some((name) => name.toLowerCase() === "content-type")) {
    headers["Content-Type"] = "application/json";
  }
  return JSON.stringify(body);
}

async function httpRequest(request: HttpRequest): Promise<HttpResponse> {
  const method = request.method ?? "GET";
  const headers = { Accept: "application/json", ...request.headers };
  const body = encodeBody(request.body, headers);

  if (isTauri()) {
    try {
      const response = await invoke<{ status: number; ok: boolean; url: string; content_type: string | null; body: string }>(
        "http_request",
        {
          request: {
            method,
            url: request.url,
            headers,
            body,
            timeout_ms: request.timeoutMs,
            backend_tls: request.backendTls ?? false,
          },
        },
      );
      return { status: response.status, ok: response.ok, url: response.url, contentType: response.content_type, body: response.body };
    } catch (error) {
      const message = String(error).replace(/^network:\s*/, "");
      throw new ApiError(`Could not reach ${hostOf(request.url)}: ${message}`, "network", undefined, request.url);
    }
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), request.timeoutMs ?? 20_000);
  try {
    const response = await fetch(request.url, { method, headers, body, signal: controller.signal });
    return {
      status: response.status,
      ok: response.ok,
      url: response.url || request.url,
      contentType: response.headers.get("content-type"),
      body: await response.text(),
    };
  } catch (error) {
    throw new ApiError(`Could not reach ${hostOf(request.url)}: ${String(error)}`, "network", undefined, request.url);
  } finally {
    clearTimeout(timer);
  }
}

/** Parse a JSON response body, with a readable error when the server sent HTML or text. */
function parseJson<T>(response: HttpResponse): T {
  if (!response.body) return null as T;
  try {
    return JSON.parse(response.body) as T;
  } catch {
    throw new ApiError(
      `Unexpected non-JSON response from ${hostOf(response.url)} (HTTP ${response.status}). Check the server address.`,
      "parse",
      response.status,
      response.url,
    );
  }
}

/** Request and parse JSON. HTTP error statuses are returned, not thrown. */
export async function requestJson<T>(request: HttpRequest): Promise<{ status: number; ok: boolean; data: T }> {
  const response = await httpRequest(request);
  return { status: response.status, ok: response.ok, data: parseJson<T>(response) };
}

function hostOf(url: string) {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}
