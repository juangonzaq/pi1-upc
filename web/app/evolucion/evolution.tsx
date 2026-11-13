"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { cropUrl, useApi } from "@/lib/api";
import { MODEL_STATUS, dateTime, num, pct } from "@/lib/format";
import type { Evolution, EvolutionExample, EvolutionExamples, EvolutionPoint, EvolutionVerdict, FieldType } from "@/lib/types";
import { SetupHelp } from "@/components/setup-help";
import { Card, Empty, Icon, Loading, Notice, PageHeader, Spinner, cx } from "@/components/ui";

const of100 = (x: number) => Math.round(100 * x);
const statusText = (p: EvolutionPoint) => [p.active ? "En uso" : MODEL_STATUS[p.status] ?? p.status, ...(p.has_weights ? [] : ["sin pesos"])].join(", ");

export function EvolutionView() {
  const { data, error } = useApi<Evolution>("/api/evolution");
  const comparison = useApi<EvolutionExamples>("/api/evolution/examples");
  if (error && !data) return <SetupHelp error={error} />;
  if (!data) return <Loading />;

  const points = data.points;
  const header = (
    <PageHeader
      title="Evolución del modelo"
      description={data.documents ? `Cada versión se mide sobre los mismos ${data.documents} formatos, con su respuesta correcta conocida.` : "Cada versión se mide sobre el mismo conjunto de prueba, con su respuesta correcta conocida."}
    />
  );
  if (points.length < 2) {
    return (
      <div>
        {header}
        <Empty>Hacen falta al menos dos versiones medidas sobre los mismos formatos para ver una mejora.</Empty>
      </div>
    );
  }

  const line = points.filter((p) => p.status !== "rejected");
  const first = line[0];
  const last = line[line.length - 1];
  const noCycles = !points.some((p) => p.kind === "cycle");
  const lost = points.filter((p) => !p.has_weights);

  return (
    <div className="space-y-8">
      <div>
        {header}
        {(noCycles || !data.real_handwriting || lost.length > 0) && (
          <Notice tone="amber">
            {noCycles && "Aún no hay reentrenamientos con correcciones. "}
            {!data.real_handwriting && "Formatos generados, no escritura real. "}
            {lost.length > 0 && `${lost.map((p) => p.label).join(", ")} ya no tiene sus pesos: esa medición no se puede repetir.`}
          </Notice>
        )}
      </div>

      <Card padded={false}>
        <div className="grid lg:grid-cols-[17rem_minmax(0,1fr)]">
          <div className="flex flex-col justify-center border-b border-line px-6 py-6 lg:border-b-0 lg:border-r">
            <div className="font-display text-[4.5rem] font-medium leading-none text-ink">
              {of100(last.accuracy)}<span className="ml-2 text-2xl text-ink-3">de 100</span>
            </div>
            <p className="mt-3 text-sm text-ink-2">caracteres bien leídos por {last.label}</p>
            <p className="mt-4 border-t border-line pt-3 text-sm text-ink-3">{first.label}: {of100(first.accuracy)} de 100</p>
          </div>
          <TrendChart points={points} />
        </div>
      </Card>

      <div className="grid gap-8 lg:grid-cols-2">
        <Card title="Qué tipo de dato mejoró" subtitle="Caracteres bien leídos, de cada 100">
          <Dumbbells first={first} last={last} />
        </Card>
        <Balance data={comparison.data} error={comparison.error} />
      </div>

      {comparison.data && <Gallery data={comparison.data} />}

      <details className="border border-line bg-surface">
        <summary className="cursor-pointer px-5 py-3.5 text-sm text-ink-2 hover:text-ink">Ver los datos en tabla</summary>
        <div className="overflow-x-auto border-t border-line">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[0.8rem] text-ink-3">
                <th className="px-5 py-2.5 font-normal">Versión</th>
                <th className="px-5 py-2.5 text-right font-normal">Caracteres bien leídos</th>
                <th className="px-5 py-2.5 text-right font-normal">CER</th>
                <th className="px-5 py-2.5 text-right font-normal">Campos exactos</th>
                <th className="px-5 py-2.5 font-normal">Estado</th>
              </tr>
            </thead>
            <tbody>
              {points.map((p) => (
                <tr key={p.version} className="border-t border-line">
                  <td className="px-5 py-2.5"><div>{p.label}</div><div className="font-mono text-[0.75rem] text-ink-3">{p.version}</div></td>
                  <td className="tabular px-5 py-2.5 text-right">{pct(p.accuracy)}</td>
                  <td className="tabular px-5 py-2.5 text-right">{num(p.cer)}</td>
                  <td className="tabular px-5 py-2.5 text-right">{pct(p.exact)}</td>
                  <td className="px-5 py-2.5 text-ink-2">{statusText(p)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

/** Ancho real del contenedor: el SVG se dibuja a escala 1:1 para que el texto no cambie de tamaño. */
function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}

const HEIGHT = 340;
const MARGIN = { top: 34, right: 20, bottom: 66, left: 46 };
const TICKS = [0, 0.25, 0.5, 0.75, 1];

function TrendChart({ points }: { points: EvolutionPoint[] }) {
  const { ref, width } = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const plot = Math.max(0, width - MARGIN.left - MARGIN.right);
  const span = Math.min(plot * 0.76, 300 * (points.length - 1));  // con pocas versiones no se estira: aplanaría la pendiente
  const inset = (plot - span) / 2;
  const x = (i: number) => MARGIN.left + inset + (points.length > 1 ? (i * span) / (points.length - 1) : span / 2);
  const y = (v: number) => MARGIN.top + (1 - v) * (HEIGHT - MARGIN.top - MARGIN.bottom);
  const bottom = y(0);
  const line = points.map((p, i) => ({ p, i })).filter(({ p }) => p.status !== "rejected");
  const path = line.map(({ p, i }, k) => `${k ? "L" : "M"}${x(i)},${y(p.accuracy)}`).join(" ");
  const area = `${path} L${x(line[line.length - 1].i)},${bottom} L${x(line[0].i)},${bottom} Z`;
  const band = points.length > 1 ? span / (points.length - 1) : plot;
  const compact = band < 120;
  const labelled = new Set([line[0].i, line[line.length - 1].i]);
  const shown = hover === null ? null : points[hover];

  return (
    <div ref={ref} className="relative px-1 pb-2 pt-3" onPointerLeave={() => setHover(null)}>
      {width > 0 && (
        <svg width={width} height={HEIGHT} role="img" aria-label={`Caracteres bien leídos por versión: ${points.map((p) => `${p.label} ${of100(p.accuracy)} de 100`).join(", ")}`}>
          {TICKS.map((t) => (
            <g key={t}>
              <line x1={MARGIN.left} x2={width - MARGIN.right} y1={y(t)} y2={y(t)} stroke="var(--color-line)" strokeWidth={1} />
              <text x={MARGIN.left - 10} y={y(t) + 4} textAnchor="end" fontSize={12} fill="var(--color-ink-3)" className="tabular">{100 * t}</text>
            </g>
          ))}
          {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={MARGIN.top} y2={bottom} stroke="var(--color-silver)" strokeWidth={1} />}
          <path d={area} fill="var(--color-gold)" opacity={0.1} />
          <path d={path} pathLength={1} fill="none" stroke="var(--color-gold-ink)" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="draw-line" />
          {points.map((p, i) => {
            const rejected = p.status === "rejected";
            return (
              <g key={p.version}>
                <circle
                  cx={x(i)}
                  cy={y(p.accuracy)}
                  r={hover === i ? 7 : 6}
                  fill={rejected ? "var(--color-surface)" : "var(--color-gold-ink)"}
                  stroke={rejected ? "var(--color-ink-3)" : "var(--color-surface)"}
                  strokeWidth={2}
                />
                {labelled.has(i) && (
                  <text x={x(i)} y={y(p.accuracy) - 16} textAnchor="middle" fontSize={14} fontWeight={500} fill="var(--color-ink)">{of100(p.accuracy)}</text>
                )}
                <text x={x(i)} y={bottom + 24} textAnchor="middle" fontSize={13} fill="var(--color-ink)">{compact ? p.short : p.label}</text>
                {!compact && <text x={x(i)} y={bottom + 43} textAnchor="middle" fontSize={12} fill="var(--color-ink-3)">{statusText(p)}</text>}
                <rect
                  x={x(i) - band / 2}
                  y={MARGIN.top - 10}
                  width={band}
                  height={HEIGHT - MARGIN.top - 4}
                  fill="transparent"
                  tabIndex={0}
                  role="img"
                  aria-label={`${p.label}: ${of100(p.accuracy)} de 100 caracteres bien leídos, ${statusText(p)}`}
                  onPointerEnter={() => setHover(i)}
                  onPointerDown={() => setHover(i)}
                  onFocus={() => setHover(i)}
                  onBlur={() => setHover(null)}
                  style={{ outline: "none" }}
                />
              </g>
            );
          })}
        </svg>
      )}
      {shown && hover !== null && (
        <div
          className="pointer-events-none absolute z-10 w-56 border border-line bg-surface px-3.5 py-2.5 text-sm shadow-sm"
          style={{ left: x(hover) + 240 < width ? x(hover) + 16 : Math.max(8, x(hover) - 240), top: Math.max(8, y(shown.accuracy) - 40) }}
        >
          <div className="font-medium text-ink">{shown.label}</div>
          <div className="text-[0.8rem] text-ink-3">{dateTime(shown.created_at)}, {statusText(shown)}</div>
          <dl className="mt-1.5 grid grid-cols-[1fr_auto] gap-x-3 text-[0.82rem] text-ink-2">
            <dt>Caracteres bien leídos</dt><dd className="tabular text-ink">{pct(shown.accuracy, 0)}</dd>
            <dt>Campos exactos</dt><dd className="tabular text-ink">{pct(shown.exact, 0)}</dd>
          </dl>
        </div>
      )}
    </div>
  );
}

const TYPE_NAMES: [FieldType, string][] = [["numeric", "Números"], ["time", "Horas"], ["date", "Fechas"], ["code", "Códigos"], ["free_text", "Texto libre"]];

function Dumbbells({ first, last }: { first: EvolutionPoint; last: EvolutionPoint }) {
  const rows = TYPE_NAMES.flatMap(([type, name]) => {
    const a = first.by_type[type];
    const b = last.by_type[type];
    return a && b ? [{ name, a: 100 * a.accuracy, b: 100 * b.accuracy }] : [];
  });
  return (
    <div>
      <div className="mb-4 flex flex-wrap gap-x-5 gap-y-1 text-[0.82rem] text-ink-2">
        <span className="inline-flex items-center gap-2"><span className="size-2.5 rounded-full bg-silver" aria-hidden />{first.label}</span>
        <span className="inline-flex items-center gap-2"><span className="size-2.5 rounded-full bg-gold-ink" aria-hidden />{last.label}</span>
      </div>
      <ul className="space-y-1">
        {rows.map((r) => (
          <li key={r.name} tabIndex={0} className="group relative grid grid-cols-[5.5rem_minmax(0,1fr)_2rem] items-center gap-3 py-1.5 outline-none" aria-label={`${r.name}: de ${Math.round(r.a)} a ${Math.round(r.b)} de 100`}>
            <span className="text-sm text-ink">{r.name}</span>
            <span className="relative h-5">
              <span className="absolute inset-x-0 top-1/2 h-px bg-line" aria-hidden />
              <span className="absolute top-1/2 h-0.5 -translate-y-1/2 bg-silver" style={{ left: `${Math.min(r.a, r.b)}%`, width: `${Math.abs(r.b - r.a)}%` }} aria-hidden />
              <span className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-silver ring-2 ring-surface" style={{ left: `${r.a}%` }} aria-hidden />
              <span className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-gold-ink ring-2 ring-surface" style={{ left: `${r.b}%` }} aria-hidden />
              <span
                className="pointer-events-none absolute bottom-full z-10 mb-1 hidden -translate-x-1/2 whitespace-nowrap border border-line bg-surface px-2.5 py-1 text-[0.8rem] text-ink-2 shadow-sm group-hover:block group-focus:block"
                style={{ left: `${Math.min(80, Math.max(20, r.b))}%` }}
              >
                De {Math.round(r.a)} a {Math.round(r.b)} de 100
              </span>
            </span>
            <span className="tabular text-right text-sm font-medium text-ink">{Math.round(r.b)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const VERDICTS: { key: EvolutionVerdict; label: string; icon: "check" | "error" | "review"; fill: string; text: string; mark: string }[] = [
  { key: "improved", label: "Mejoró", icon: "check", fill: "bg-auto", text: "text-white", mark: "text-auto" },
  { key: "same_right", label: "Ya leía bien", icon: "check", fill: "bg-silver", text: "text-ink", mark: "text-ink-3" },
  { key: "same_wrong", label: "Sigue mal", icon: "review", fill: "bg-review", text: "text-ink", mark: "text-review" },
  { key: "worse", label: "Empeoró", icon: "error", fill: "bg-error", text: "text-white", mark: "text-error" },
];

function Balance({ data, error }: { data: EvolutionExamples | null; error: string | null }) {
  const title = "Recorte por recorte";
  if (!data) {
    return (
      <Card title={title}>
        {error ? <p className="text-sm text-ink-3">{error}</p> : <p className="flex items-center gap-2 text-sm text-ink-3"><Spinner /> Comparando recortes</p>}
      </Card>
    );
  }
  if (!data.available || !data.counts) return <Card title={title}><p className="text-sm text-ink-3">Esta comparación necesita las lecturas de ambas versiones.</p></Card>;
  const total = data.n ?? 0;
  const parts = VERDICTS.map((v) => ({ ...v, n: data.counts?.[v.key] ?? 0 })).filter((v) => v.n > 0);
  return (
    <Card title={title} subtitle={`${total} recortes al azar, de ${data.from} a ${data.to}`}>
      <div className="flex gap-0.5" role="img" aria-label={parts.map((p) => `${p.label}: ${p.n} de ${total}`).join(", ")}>
        {parts.map((p) => {
          const share = (100 * p.n) / total;
          return (
            <div key={p.key} title={`${p.label}: ${p.n} de ${total}`} className={cx("flex h-7 items-center rounded-[4px] px-2 text-[0.8rem] font-medium", p.fill, p.text)} style={{ width: `${share}%` }}>
              {share >= 9 && `${Math.round(share)} %`}
            </div>
          );
        })}
      </div>
      <ul className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-[0.85rem] text-ink-2">
        {parts.map((p) => (
          <li key={p.key} className="inline-flex items-center gap-1.5"><Icon name={p.icon} className={cx("size-3.5", p.mark)} />{p.label}</li>
        ))}
      </ul>
    </Card>
  );
}

function Gallery({ data }: { data: EvolutionExamples }) {
  if (!data.available || !data.examples?.length) return null;
  return (
    <section>
      <h2 className="font-display text-xl font-medium">Mismo recorte, dos lecturas</h2>
      <p className="mt-1 text-sm text-ink-2">Ejemplos de esos recortes, incluidos casos en que empeoró.</p>
      <ul className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {data.examples.map((e) => <ExampleCard key={e.crop} e={e} from={data.from ?? "Antes"} to={data.to ?? "Después"} />)}
      </ul>
    </section>
  );
}

function ExampleCard({ e, from, to }: { e: EvolutionExample; from: string; to: string }) {
  const beforeRight = e.verdict === "worse";
  return (
    <li className="border border-line bg-surface">
      <div className="flex h-24 items-center justify-center border-b border-line bg-paper-2 p-3">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={cropUrl(e.crop)} alt={`Recorte de ${e.label}`} className="crop-img max-h-full w-auto max-w-full border border-line object-contain" />
      </div>
      <dl className="divide-y divide-line text-sm">
        <Reading who={from} text={e.before} right={beforeRight} />
        <Reading who={to} text={e.after} right={!beforeRight} />
      </dl>
    </li>
  );
}

function Reading({ who, text, right }: { who: ReactNode; text: string; right: boolean }) {
  return (
    <div className="px-3.5 py-2.5">
      <dt className="text-[0.75rem] text-ink-3">{who}</dt>
      <dd className="mt-0.5 flex items-center gap-2">
        <Icon name={right ? "check" : "error"} className={cx("size-3.5 shrink-0", right ? "text-auto" : "text-error")} />
        <span className={cx("min-w-0 truncate font-mono", right ? "text-ink" : "text-ink-3 line-through")} title={text}>{text || "sin lectura"}</span>
      </dd>
    </div>
  );
}
