"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useApi } from "@/lib/api";
import { BATCH_LABEL, STATE_LABEL, dateTime, shortId } from "@/lib/format";
import type { DocState, DocSummary } from "@/lib/types";
import { NextReviewButton } from "@/components/next-review";
import { SetupHelp } from "@/components/setup-help";
import { Empty, Icon, Loading, Meter, PageHeader, Segmented, StateBadge, cx, inputClass } from "@/components/ui";

const FILTERS: (DocState | "")[] = ["EN_REVISION", "VALIDADO", "APROBADO", "ERROR", "DUPLICADO_PENDIENTE", ""];
const FILTER_LABEL: Partial<Record<DocState | "", string>> = { EN_REVISION: "Por revisar", VALIDADO: "Por aprobar", APROBADO: "Aprobados", ERROR: "Con error", DUPLICADO_PENDIENTE: "Duplicados", "": "Todos" };

export function DocumentList() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const state = (params.get("estado") ?? "") as DocState | "";
  const batch = params.get("lote") ?? "";
  const [q, setQ] = useState(params.get("q") ?? "");

  const query = new URLSearchParams();
  if (state) query.set("state", state);
  if (batch) query.set("batch", batch);
  if (q.trim()) query.set("q", q.trim());
  const { data, error, loading } = useApi<{ total: number; documents: DocSummary[] }>(`/api/documents?${query}`);
  const counts = useApi<{ documents: number; by_state: Partial<Record<DocState, number>> }>("/api/status");

  function setParam(key: string, value: string) {
    const next = new URLSearchParams(params.toString());
    if (value) next.set(key, value);
    else next.delete(key);
    router.replace(`${pathname}?${next}`, { scroll: false });
  }

  return (
    <div>
      <PageHeader
        title="Revisión"
        description="Cada formato procesado, con lo que el modelo aceptó solo y lo que espera una decisión. Abra uno para revisarlo campo por campo."
        actions={(counts.data?.by_state.EN_REVISION ?? 0) > 0 ? <NextReviewButton /> : undefined}
      />

      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <Segmented
          label="Filtrar por estado"
          value={state}
          onChange={(v) => setParam("estado", v)}
          options={FILTERS.map((f) => ({ value: f, label: FILTER_LABEL[f] ?? STATE_LABEL[f as DocState], count: f ? counts.data?.by_state[f] ?? 0 : counts.data?.documents }))}
        />
        <div className="flex gap-2">
          <select aria-label="Lote" className={cx(inputClass, "h-9 w-48")} value={batch} onChange={(e) => setParam("lote", e.target.value)}>
            <option value="">Todos los lotes</option>
            {Object.entries(BATCH_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <div className="relative">
            <Icon name="search" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-3" />
            <input aria-label="Buscar" placeholder="Buscar" className={cx(inputClass, "h-9 w-56 pl-9")} value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
        </div>
      </div>

      {error && !data ? (
        <SetupHelp error={error} />
      ) : !data ? (
        <Loading />
      ) : data.documents.length === 0 ? (
        <Empty action={<Link href="/reconocer" className="text-sm font-medium text-gold-ink underline underline-offset-4">Reconocer un formato</Link>}>
          {state ? `No hay formatos en «${FILTER_LABEL[state] ?? STATE_LABEL[state]}».` : "Todavía no hay formatos."}
        </Empty>
      ) : (
        <div className="overflow-x-auto border border-line bg-surface">
          <table className="w-full text-sm">
            <caption className="sr-only">Formatos</caption>
            <thead>
              <tr className="border-b border-line text-left text-ink-3">
                <th className="px-5 py-3 font-normal">Formato</th>
                <th className="px-5 py-3 font-normal">Fecha, guardia y equipo</th>
                <th className="px-5 py-3 font-normal">Estado</th>
                <th className="w-56 px-5 py-3 font-normal">Campos a revisión</th>
                <th className="px-5 py-3 font-normal">Cargado</th>
                <th className="px-5 py-3"><span className="sr-only">Acción</span></th>
              </tr>
            </thead>
            <tbody className={cx(loading && "opacity-60")}>
              {data.documents.map((d) => (
                <tr key={d.id} className="border-b border-line last:border-0 hover:bg-paper-2">
                  <td className="px-5 py-3">
                    <Link href={`/documentos/${d.id}`} className="font-medium text-ink hover:underline">
                      {d.form_id ?? d.source_name}
                    </Link>
                    <div className="text-[0.8rem] text-ink-3">
                      {BATCH_LABEL[d.batch ?? ""] ?? d.batch ?? "Sin lote"}
                      {d.rescan && ", reescaneo"}
                    </div>
                  </td>
                  <td className="tabular px-5 py-3 text-ink-2">{d.business_key ? d.business_key.split("|").join("  ") : <span className="text-ink-3">sin clave</span>}</td>
                  <td className="px-5 py-3">
                    <StateBadge state={d.state} />
                    {d.state === "ERROR" && d.cause && <div className="mt-1 max-w-xs truncate text-[0.8rem] text-ink-3" title={d.cause}>{d.cause}</div>}
                  </td>
                  <td className="px-5 py-3">
                    {d.fields ? (
                      <div>
                        <div className="tabular mb-1 text-[0.8rem] text-ink-2">{d.review} de {d.fields}</div>
                        <Meter value={d.review / d.fields} status="review" label={`${d.review} de ${d.fields} campos a revisión`} />
                      </div>
                    ) : (
                      <span className="text-ink-3">sin campos</span>
                    )}
                  </td>
                  <td className="px-5 py-3 text-ink-2">
                    {dateTime(d.uploaded_at)}
                    <div className="text-[0.8rem] text-ink-3">{d.uploaded_by}</div>
                  </td>
                  <td className="px-5 py-3 text-right">
                    <Link href={`/documentos/${d.id}`} className="text-sm font-medium text-gold-ink hover:underline" aria-label={`Abrir ${d.form_id ?? shortId(d.id)}`}>
                      {d.state === "EN_REVISION" && d.fields ? "Revisar" : d.state === "VALIDADO" ? "Aprobar" : "Abrir"}
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.total > data.documents.length && <div className="border-t border-line px-5 py-3 text-sm text-ink-3">Se muestran {data.documents.length} de {data.total}. Use la búsqueda para acotar.</div>}
        </div>
      )}
    </div>
  );
}
