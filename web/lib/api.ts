"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// La API corre en local (perfo serve); 127.0.0.1 evita que "localhost" se resuelva a IPv6.
export const API_URL = (process.env.NEXT_PUBLIC_PERFO_API ?? "http://127.0.0.1:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public fields: string[] = [],
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, init);
  } catch {
    throw new ApiError(0, `No se pudo conectar con la API en ${API_URL}. Inicie el servidor con "make api".`);
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = typeof data?.detail === "string" ? data.detail : Array.isArray(data?.detail) ? data.detail.map((d: { msg: string }) => d.msg).join("; ") : res.statusText;
    throw new ApiError(res.status, detail || `Error ${res.status}`, data?.fields ?? []);
  }
  return data as T;
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  return api<T>(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body ?? {}) });
}

export function postForm<T>(path: string, form: FormData): Promise<T> {
  return api<T>(path, { method: "POST", body: form });
}

export const cropUrl = (key: string) => `${API_URL}/api/objects/${key}`;
export const docImageUrl = (id: string, w = 1600) => `${API_URL}/api/documents/${id}/image?w=${w}`;
export const sampleImageUrl = (id: string, w = 480) => `${API_URL}/api/samples/${id}/image?w=${w}`;

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** GET con estado de carga; `path = null` no consulta. `reload` vuelve a pedir sin vaciar los datos. */
export function useApi<T>(path: string | null) {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: !!path });
  const [tick, setTick] = useState(0);
  const current = useRef(path);

  useEffect(() => {
    current.current = path;
    if (!path) return;
    let alive = true;
    api<T>(path)
      .then((data) => alive && setState({ data, error: null, loading: false }))
      .catch((e) => alive && setState((s) => ({ data: s.data, error: errorText(e), loading: false })));
    return () => {
      alive = false;
    };
  }, [path, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { ...state, reload };
}

/** El formato más antiguo que espera revisión (con campos reconocidos), excluyendo `except`. */
export async function nextToReview(except?: string): Promise<string | null> {
  const r = await api<{ documents: { id: string; uploaded_at: string; fields: number }[] }>("/api/documents?state=EN_REVISION&limit=5000");
  const pending = r.documents.filter((d) => d.fields > 0 && d.id !== except).sort((a, b) => a.uploaded_at.localeCompare(b.uploaded_at));
  return pending[0]?.id ?? null;
}

/** Repite `fn` cada `ms` mientras el componente está visible. */
export function useInterval(fn: () => void, ms: number | null) {
  const saved = useRef(fn);
  useEffect(() => {
    saved.current = fn;
  }, [fn]);
  useEffect(() => {
    if (ms === null) return;
    const id = setInterval(() => saved.current(), ms);
    return () => clearInterval(id);
  }, [ms]);
}
