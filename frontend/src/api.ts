import { useCallback, useEffect, useState } from "react";
export function isExternalAccess(): boolean {
  return ["external-viewer", "external-engineer"].includes(document.querySelector('meta[name="axiom-access-mode"]')?.getAttribute("content") ?? "");
}
export function isExternalViewer(): boolean {
  return document.querySelector('meta[name="axiom-access-mode"]')?.getAttribute("content") === "external-viewer";
}
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); this.name = 'ApiError'; }
}
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      message =
        typeof body.detail === "string"
          ? body.detail
          : Array.isArray(body.detail)
            ? body.detail
                .map(
                  (issue: { loc?: string[]; msg?: string }) =>
                    `${issue.loc?.filter((part) => part !== "body").join(" · ") || "Input"}: ${issue.msg || "Invalid value"}`,
                )
                .join(". ")
            : JSON.stringify(body.detail ?? body);
    } catch {
      /* retain HTTP message */
    }
    if (response.status === 401)
      message += isExternalAccess()
        ? ". Sign in again with the external access password."
        : ". Open Workspace settings to enter the API bearer token.";
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}
export function useResource<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [errorStatus, setErrorStatus] = useState<number | null>(null),
    [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    setData(null);
    setError("");
    setErrorStatus(null);
    if (!path) {
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    api<T>(path)
      .then((value) => {
        if (active) setData(value);
      })
      .catch((e) => {
        if (active) {
          setErrorStatus(e instanceof ApiError ? e.status : null);
          setError(
            e instanceof Error ? e.message : "Could not connect to the API",
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [path, revision]);
  return { data, loading, error, errorStatus, reload, setData };
}
export function formatDate(value: string) {
  return new Date(value).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
export function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
export function shortId(id: string) {
  return id.replace(/^run[_-]/, "").slice(0, 8);
}

export function authHeaders(): Record<string, string> {
  if (isExternalAccess()) return {};
  const token = sessionStorage.getItem("axiom.apiToken");
  return token ? { Authorization: `Bearer ${token}` } : {};
}
