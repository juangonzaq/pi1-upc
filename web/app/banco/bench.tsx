"use client";

import Link from "next/link";
import { useMemo, useState, type FormEvent } from "react";
import { cropUrl, errorText, post, postForm, useApi } from "@/lib/api";
import { MODEL_STATUS, ORIGIN_LABEL, TYPE_LABEL, displayLabel, num, pct } from "@/lib/format";
import type { BenchCrop, BenchModelResult, BenchRow, FieldType, ModelsResponse } from "@/lib/types";
import { SetupHelp } from "@/components/setup-help";
import { Button, Empty, ErrorBox, Icon, Label, Loading, Meter, PageHeader, RouteBadge, cx, inputClass } from "@/components/ui";

type Results = Record<string, BenchModelResult>;

export function Bench() {
  const models = useApi<ModelsResponse>("/api/models");
  const [fieldType, setFieldType] = useState("");
  const [source, setSource] = useState("no_vistos");
  const [n, setN] = useState(16);
  const [seed, setSeed] = useState(1);
  const [chosen, setChosen] = useState<string[] | null>(null);
  const [results, setResults] = useState<{ ids: string[]; data: Results } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const query = `/api/bench/crops?n=${n}&seed=${seed}&source=${source}${fieldType ? `&field_type=${fieldType}` : ""}`;
  const crops = useApi<{ crops: BenchCrop[] }>(query);

  // por defecto: línea base frente al modelo activo
  const defaults = useMemo(() => {
    const list = models.data?.models ?? [];
    const base = list.find((m) => !m.parent);
    const active = list.find((m) => m.active);
    return [base?.version, active?.version].filter((v, i, a): v is string => !!v && a.indexOf(v) === i);
  }, [models.data]);
  const versions = chosen ?? defaults;

  function toggle(v: string) {
    setChosen((prev) => {
      const cur = prev ?? defaults;
      return cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v].slice(-4);
    });
  }

  async function run() {
    if (!crops.data?.crops.length) return;
    setBusy(true);
    setError(null);
    try {
      const ids = crops.data.crops.map((c) => c.crop_id);
      const r = await post<{ results: Results }>("/api/bench/run", { crop_ids: ids, versions });
      setResults({ ids, data: r.results });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  if (models.error && !models.data) return <SetupHelp error={models.error} />;
  const sample = crops.data?.crops ?? [];
  const current = results && results.ids.join() === sample.map((c) => c.crop_id).join() ? results.data : null;

  return (
    <div>
      <PageHeader
        title="Banco de recortes"
        description="Pone a prueba solo al lector: los mismos recortes, leídos por distintas versiones del modelo y comparados con la verdad conocida."
      />

      <section aria-label="Muestra y modelos" className="border border-line bg-surface">
        <div className="grid gap-4 px-5 py-5 md:grid-cols-[1fr_1fr_8rem_auto] md:items-end">
          <div>
            <Label htmlFor="ftype">Tipo de campo</Label>
            <select id="ftype" className={inputClass} value={fieldType} onChange={(e) => setFieldType(e.target.value)}>
              <option value="">Todos</option>
              {Object.entries(TYPE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div>
            <Label htmlFor="source">Origen</Label>
            <select id="source" className={inputClass} value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="no_vistos">Formatos no vistos al entrenar</option>
              <option value="entrenamiento">Partición de entrenamiento</option>
              <option value="todos">Todos</option>
            </select>
          </div>
          <div>
            <Label htmlFor="n">Cantidad</Label>
            <select id="n" className={inputClass} value={n} onChange={(e) => setN(Number(e.target.value))}>
              {[8, 16, 32, 48].map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
          </div>
          <Button variant="secondary" onClick={() => setSeed((s) => s + 1)}>
            <Icon name="refresh" /> Otra muestra
          </Button>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-4 border-t border-line px-5 py-4">
          <fieldset className="flex flex-wrap items-center gap-2">
            <legend className="sr-only">Versiones a comparar</legend>
            {models.data ? (
              models.data.models.map((m) => {
                const on = versions.includes(m.version);
                return (
                  <label key={m.version} className={cx("flex cursor-pointer items-center gap-2 border px-3 py-1.5 text-sm", on ? "border-ink bg-paper-2" : "border-line text-ink-2 hover:border-ink-3")}>
                    <input type="checkbox" className="accent-[#24211f]" checked={on} onChange={() => toggle(m.version)} />
                    {m.version}
                    <span className="text-[0.78rem] text-ink-3">{MODEL_STATUS[m.status] ?? m.status}</span>
                  </label>
                );
              })
            ) : (
              <Loading />
            )}
          </fieldset>
          <Button onClick={run} busy={busy} disabled={!sample.length || !versions.length}>
            Leer {sample.length} recortes
          </Button>
        </div>
        {error && <div className="border-t border-line px-5 py-4"><ErrorBox>{error}</ErrorBox></div>}
      </section>

      {current && (
        <section className="mt-8" aria-label="Comparación">
          <h2 className="font-display text-xl font-medium">Comparación</h2>
          <div className="mt-4 overflow-x-auto border border-line bg-surface">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-ink-3">
                  <th className="px-5 py-3 font-normal">Versión</th>
                  <th className="w-64 px-5 py-3 font-normal">Exactitud literal</th>
                  <th className="px-5 py-3 text-right font-normal">CER</th>
                  <th className="px-5 py-3 text-right font-normal">Se aceptarían solos</th>
                  <th className="px-5 py-3 text-right font-normal">Errores entre los aceptados</th>
                  <th className="px-5 py-3 text-right font-normal">Tiempo por recorte</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(current).map(([v, r]) => (
                  <tr key={v} className="border-b border-line last:border-0">
                    <td className="px-5 py-3 font-medium">{v}</td>
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-3">
                        <Meter value={r.summary.exact ?? 0} status="auto" className="flex-1" label={`Exactitud de ${v}`} />
                        <span className="tabular w-14 text-right">{pct(r.summary.exact, 0)}</span>
                      </div>
                    </td>
                    <td className="tabular px-5 py-3 text-right">{num(r.summary.cer, 3)}</td>
                    <td className="tabular px-5 py-3 text-right">{r.summary.auto} de {r.summary.n}</td>
                    <td className="tabular px-5 py-3 text-right">
                      <span className="inline-flex items-center gap-1.5">
                        {r.summary.auto_errors > 0 && <Icon name="error" className="size-3.5 text-error" />}
                        {r.summary.auto_errors}
                      </span>
                    </td>
                    <td className="tabular px-5 py-3 text-right text-ink-2">{Math.round(r.ms_per_crop)} ms</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="mt-8" aria-label="Recortes">
        <div className="flex items-baseline justify-between">
          <h2 className="font-display text-xl font-medium">Recortes</h2>
          <span className="text-sm text-ink-3">{crops.loading ? "Buscando recortes" : `${sample.length} en la muestra`}</span>
        </div>
        <div className="mt-4">
          {crops.error && !crops.data ? (
            <ErrorBox>{crops.error}</ErrorBox>
          ) : !crops.data ? (
            <Loading />
          ) : sample.length === 0 ? (
            <Empty action={<Link href="/reconocer" className="text-sm font-medium text-gold-ink underline underline-offset-4">Reconocer un formato</Link>}>
              No hay recortes con este filtro. Procese algunos formatos primero.
            </Empty>
          ) : (
            <div className="overflow-x-auto border border-line bg-surface">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-ink-3">
                    <th className="px-5 py-3 font-normal">Recorte</th>
                    <th className="px-5 py-3 font-normal">Campo</th>
                    <th className="px-5 py-3 font-normal">Verdad</th>
                    {current && Object.keys(current).map((v) => <th key={v} className="px-5 py-3 font-normal">{v}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {sample.map((c, i) => (
                    <tr key={c.crop_id} className="border-b border-line align-top last:border-0">
                      <td className="px-5 py-3">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={cropUrl(c.crop)} alt={`Recorte de ${c.label}`} className="crop-img h-11 max-w-[220px] border border-line object-contain" loading="lazy" />
                      </td>
                      <td className="px-5 py-3">
                        <div>{displayLabel(c.label)}</div>
                        <div className="text-[0.78rem] text-ink-3">
                          {TYPE_LABEL[c.type as FieldType]}, {c.block_label}{c.row ? ` fila ${c.row}` : ""}.{" "}
                          <Link href={`/documentos/${c.document_id}`} className="hover:underline">{c.form_id}</Link> ({ORIGIN_LABEL[c.origin] ?? c.origin})
                        </div>
                      </td>
                      <td className="px-5 py-3 font-mono">{c.truth ?? <span className="font-sans text-ink-3">sin verdad</span>}</td>
                      {current && Object.entries(current).map(([v, r]) => <ResultCell key={v} row={r.rows[i]} />)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      <UploadCrop versions={versions} />
    </div>
  );
}

function ResultCell({ row }: { row: BenchRow | undefined }) {
  if (!row) return <td className="px-5 py-3" />;
  return (
    <td className="px-5 py-3">
      <div className="flex items-center gap-2">
        {row.correct !== null && <Icon name={row.correct ? "check" : "error"} className={cx("size-3.5", row.correct ? "text-auto" : "text-error")} />}
        <span className="font-mono">{row.text || <span className="font-sans text-ink-3">vacío</span>}</span>
        <span className="sr-only">{row.correct === null ? "" : row.correct ? "correcto" : "incorrecto"}</span>
      </div>
      <div className="mt-1.5 flex items-center gap-2 text-[0.78rem] text-ink-3">
        <span className="tabular">{num(row.score)}</span>
        <RouteBadge route={row.route} />
      </div>
    </td>
  );
}

function UploadCrop({ versions }: { versions: string[] }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [type, setType] = useState("numeric");
  const [truth, setTruth] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [res, setRes] = useState<Results | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const f = new FormData();
      f.set("file", file);
      f.set("field_type", type);
      f.set("versions", versions.join(","));
      if (truth.trim()) f.set("truth", truth.trim());
      const r = await postForm<{ results: Results }>("/api/bench/upload", f);
      setRes(r.results);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mt-12 border-t border-line pt-8" aria-label="Probar un recorte propio">
      <h2 className="font-display text-xl font-medium">Probar un recorte propio</h2>
      <p className="mt-1 max-w-2xl text-sm text-ink-2">Una imagen de un solo campo, por ejemplo recortada de un escaneo real. Se lee tal cual, sin alinear ni limpiar las líneas.</p>
      <form onSubmit={submit} className="mt-4 grid gap-4 md:grid-cols-[1fr_12rem_1fr_auto] md:items-end">
        <div>
          <Label htmlFor="cropfile">Imagen (PNG o JPG)</Label>
          <input
            id="cropfile"
            type="file"
            accept="image/png,image/jpeg"
            className="block w-full text-sm text-ink-2 file:mr-3 file:h-10 file:border-0 file:bg-ink file:px-4 file:text-sm file:text-white"
            onChange={(e) => {
              const f = e.target.files?.[0] ?? null;
              setFile(f);
              setRes(null);
              setPreview(f ? URL.createObjectURL(f) : null);
            }}
          />
        </div>
        <div>
          <Label htmlFor="ctype">Tipo de campo</Label>
          <select id="ctype" className={inputClass} value={type} onChange={(e) => setType(e.target.value)}>
            {Object.entries(TYPE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        <div>
          <Label htmlFor="ctruth">Texto esperado (opcional)</Label>
          <input id="ctruth" className={inputClass} value={truth} onChange={(e) => setTruth(e.target.value)} placeholder="Para marcar acierto o error" />
        </div>
        <Button type="submit" busy={busy} disabled={!file || !versions.length}>Leer recorte</Button>
      </form>
      {error && <div className="mt-4"><ErrorBox>{error}</ErrorBox></div>}
      {preview && (
        <div className="mt-5 flex flex-wrap items-start gap-8">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={preview} alt="Recorte subido" className="crop-img max-h-24 max-w-xs border border-line object-contain" />
          {res && (
            <table className="text-sm">
              <tbody>
                {Object.entries(res).map(([v, r]) => (
                  <tr key={v} className="align-top">
                    <td className="py-2 pr-5 text-ink-3">{v}</td>
                    <ResultCell row={r.rows[0]} />
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
}
