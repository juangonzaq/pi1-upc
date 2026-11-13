"use client";

import { useApi } from "@/lib/api";
import { MODEL_STATUS } from "@/lib/format";
import type { ModelsResponse } from "@/lib/types";
import { inputClass } from "./ui";

/** Selector de versión de modelo; "" significa el modelo activo. */
export function ModelSelect({ value, onChange, id }: { value: string; onChange: (v: string) => void; id?: string }) {
  const { data } = useApi<ModelsResponse>("/api/models");
  return (
    <select id={id} className={inputClass} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Modelo activo{data?.active ? ` (${data.active})` : ""}</option>
      {data?.models
        .filter((m) => !m.active)
        .map((m) => (
          <option key={m.version} value={m.version}>
            {m.version} ({(MODEL_STATUS[m.status] ?? m.status).toLowerCase()}{m.calibration ? "" : ", sin calibrar"})
          </option>
        ))}
    </select>
  );
}
