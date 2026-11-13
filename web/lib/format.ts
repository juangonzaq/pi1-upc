import type { DocState, FieldType } from "./types";

export const STATE_LABEL: Record<DocState, string> = {
  CARGADO: "Cargado",
  EN_COLA: "En cola",
  PROCESADO: "Procesado",
  EN_REVISION: "En revisión",
  VALIDADO: "Validado",
  APROBADO: "Aprobado",
  ERROR: "Error",
  DUPLICADO_PENDIENTE: "Duplicado pendiente",
};

export const TYPE_LABEL: Record<FieldType, string> = {
  numeric: "Numérico",
  code: "Código",
  time: "Hora",
  date: "Fecha",
  free_text: "Texto libre",
  master_ref: "Maestro",
};

export const BATCH_LABEL: Record<string, string> = {
  historical: "Histórico",
  operational: "Operativo",
  demo: "Demo",
  web: "Subido por web",
};

export const ORIGIN_LABEL: Record<string, string> = {
  train: "Entrenamiento",
  val_select: "Selección",
  val_calib: "Calibración",
  test: "Prueba congelada",
  operational: "Operativo",
  demo: "Demo",
  web: "Web",
};

export const STAGE_LABEL: Record<string, string> = {
  preprocess: "Preprocesamiento",
  segment: "Segmentación",
  recognize: "Reconocimiento",
  structure: "Reglas y homologación",
};

export const MODEL_STATUS: Record<string, string> = {
  active: "Activo",
  candidate: "Candidato",
  rejected: "Rechazado",
  retired: "Retirado",
  baseline: "Línea base",
};

export const VALUE_SOURCE: Record<string, string> = {
  auto: "automático",
  confirmed: "confirmado",
  corrected: "corregido",
  candidate: "candidata elegida",
};

export function pct(x: number | null | undefined, digits = 1): string {
  return x === null || x === undefined || Number.isNaN(x) ? "—" : `${(100 * x).toFixed(digits)} %`;
}

export function num(x: number | null | undefined, digits = 3): string {
  return x === null || x === undefined || Number.isNaN(x) ? "—" : x.toFixed(digits);
}

export function secs(x: number | null | undefined): string {
  if (x === null || x === undefined) return "—";
  return x < 1 ? `${Math.round(x * 1000)} ms` : `${x.toFixed(1)} s`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("es-PE", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}

/** Etiquetas de plantilla en mayúsculas se muestran en tipo oración, conservando siglas cortas como D/N o N.°. */
export function displayLabel(label: string): string {
  if (label !== label.toUpperCase() || !/[A-ZÁÉÍÓÚÑ]{3}/.test(label)) return label;
  const lower = label.toLowerCase().replace(/\(([a-z])\/([a-z])\)/g, (_, a: string, b: string) => `(${a.toUpperCase()}/${b.toUpperCase()})`).replace(/n\.°/g, "N.°");
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

export function fieldTitle(f: { block_label: string; label: string; row: number | null; table: string | null }): string {
  const label = displayLabel(f.label);
  return f.table ? `${label}, ${f.block_label.toLowerCase()} fila ${f.row}` : `${label}, ${f.block_label.toLowerCase()}`;
}
