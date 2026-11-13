import { API_URL } from "@/lib/api";
import { Card } from "./ui";

const code = "bg-paper px-1.5 py-0.5 font-mono text-[0.82rem] text-ink";

/** Se muestra cuando la API no responde: los pasos mínimos para levantarla en local. */
export function SetupHelp({ error }: { error: string }) {
  return (
    <Card title="La interfaz no encuentra la API" subtitle={error}>
      <ol className="list-decimal space-y-3 pl-5 text-sm leading-relaxed text-ink-2">
        <li>Instale el entorno, incluido el extra web: <code className={code}>make install</code></li>
        <li>Si aún no hay modelo ajustado, corra el flujo de prueba: <code className={code}>make e2e-smoke</code> (de 5 a 15 minutos)</li>
        <li>Levante la API con <code className={code}>make api</code> o, para el formato físico, <code className={code}>make dev-fisico</code>. Debe escuchar en {API_URL}.</li>
        <li>Recargue esta página.</li>
      </ol>
      <p className="mt-4 text-[0.82rem] text-ink-3">
        Si la API corre en otra dirección, defina <code className={code}>NEXT_PUBLIC_PERFO_API</code> en <code className={code}>web/.env.local</code> y reinicie la web.
      </p>
    </Card>
  );
}
