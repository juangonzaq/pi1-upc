"use client";

import Link from "next/link";
import { useApi } from "@/lib/api";
import type { Health, Status } from "@/lib/types";
import { NextReviewButton } from "@/components/next-review";
import { SetupHelp } from "@/components/setup-help";
import { Loading, Notice, PageHeader, cx, type Status as Tone } from "@/components/ui";

export function Dashboard() {
  const status = useApi<Status>("/api/status");
  const health = useApi<Health>("/api/health");

  if (status.error && !status.data) {
    return (
      <>
        <PageHeader title="Panel de operación" />
        <SetupHelp error={status.error} />
      </>
    );
  }
  if (!status.data || !health.data) return <Loading />;
  const s = status.data;
  const learn = s.learning;
  const inReview = s.by_state.EN_REVISION ?? 0;
  const errors = (s.by_state.ERROR ?? 0) + (s.by_state.DUPLICADO_PENDIENTE ?? 0);

  const stages: { label: string; value: number; detail: string; href: string; tone: Tone }[] = [
    { label: "Recibidos", value: s.documents, detail: errors ? `${errors} rechazados o duplicados` : "formatos cargados", href: "/documentos", tone: "neutral" },
    { label: "En revisión", value: inReview, detail: "esperan al validador", href: "/documentos?estado=EN_REVISION", tone: "review" },
    { label: "Validados", value: s.by_state.VALIDADO ?? 0, detail: "esperan al Jefe de Guardia", href: "/documentos?estado=VALIDADO", tone: "decided" },
    { label: "Aprobados", value: s.by_state.APROBADO ?? 0, detail: "registros cerrados", href: "/documentos?estado=APROBADO", tone: "auto" },
    { label: "Correcciones", value: learn.corrections, detail: "para el próximo reentrenamiento", href: "/modelos", tone: "gold" },
  ];

  return (
    <div>
      <PageHeader
        title="Panel de operación"
        description="Lo que llega, lo que espera una decisión y lo que alimentará el próximo reentrenamiento del modelo."
        actions={
          <>
            <Link href="/reconocer" className="inline-flex h-10 items-center border border-ink/25 bg-surface px-4 text-sm font-medium text-ink hover:border-ink">
              Reconocer un formato
            </Link>
            {inReview > 0 && <NextReviewButton />}
          </>
        }
      />

      {health.data.synthetic && (
        <div className="mb-6">
          <Notice>Este espacio de trabajo usa datos sintéticos: sirven para validar el circuito, no los indicadores del proyecto.</Notice>
        </div>
      )}

      {/* La línea del proceso: una secuencia real, de izquierda a derecha */}
      <section aria-label="Circuito de digitalización" className="border border-line bg-surface">
        <ol className="grid grid-cols-2 md:grid-cols-5">
          {stages.map((st, i) => (
            <li key={st.label} className={cx("relative", i > 0 && "border-t border-line md:border-l md:border-t-0")}>
              <Link href={st.href} className="group block h-full px-6 pb-6 pt-7 hover:bg-paper-2">
                <div className="flex items-center gap-2 text-sm text-ink-2">
                  <span className={cx("size-2.5", { neutral: "bg-silver", review: "bg-review", decided: "bg-decided", auto: "bg-auto", error: "bg-error", gold: "bg-gold" }[st.tone])} aria-hidden />
                  <span className="text-ink-3 tabular">{i + 1}</span>
                  {st.label}
                </div>
                <div className="mt-3 font-display text-[3rem] font-medium leading-none text-ink">{st.value}</div>
                <div className="mt-2 text-sm text-ink-3 group-hover:text-ink-2">{st.detail}</div>
              </Link>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
