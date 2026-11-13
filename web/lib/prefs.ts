"use client";

import { useCallback, useSyncExternalStore } from "react";

// Preferencias del navegador (solo comodidad: nombre del usuario que valida). Sin almacenamiento disponible, se usa el valor por defecto.
function read(key: string, fallback: string): string {
  try {
    return window.localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

const listeners = new Set<() => void>();

export function usePref(key: string, fallback: string): [string, (v: string) => void] {
  const value = useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => read(key, fallback),
    () => fallback,
  );
  const set = useCallback(
    (v: string) => {
      try {
        window.localStorage.setItem(key, v);
      } catch {
        /* sin almacenamiento */
      }
      listeners.forEach((l) => l());
    },
    [key],
  );
  return [value, set];
}

/** Resultado del último procesamiento de un documento (tiempos por etapa), para mostrarlo en su página. */
export function saveProcessing(docId: string, data: unknown) {
  try {
    window.sessionStorage.setItem(`perfo:process:${docId}`, JSON.stringify(data));
  } catch {
    /* sin almacenamiento */
  }
}

export function loadProcessing<T>(docId: string): T | null {
  try {
    const raw = window.sessionStorage.getItem(`perfo:process:${docId}`);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

/** Decisiones de revisión aún sin validar. Valen solo para la lectura en la que se tomaron. */
export function saveDraft(docId: string, runId: string, decisions: object) {
  try {
    const key = `perfo:draft:${docId}`;
    if (Object.keys(decisions).length) window.localStorage.setItem(key, JSON.stringify({ runId, decisions }));
    else window.localStorage.removeItem(key);
  } catch {
    /* sin almacenamiento */
  }
}

export function loadDraft<T>(docId: string, runId: string): Record<string, T> {
  try {
    const saved = JSON.parse(window.localStorage.getItem(`perfo:draft:${docId}`) ?? "null");
    return saved?.runId === runId ? saved.decisions : {};
  } catch {
    return {};
  }
}
