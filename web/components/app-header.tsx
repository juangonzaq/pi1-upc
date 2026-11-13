"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { API_URL, useApi, useInterval } from "@/lib/api";
import type { Health } from "@/lib/types";
import { Icon, Spinner, cx } from "./ui";

const NAV = [
  { href: "/", label: "Panel" },
  { href: "/reconocer", label: "Reconocer" },
  { href: "/documentos", label: "Revisión" },
  { href: "/banco", label: "Banco de recortes" },
  { href: "/modelos", label: "Modelos" },
  { href: "/evolucion", label: "Evolución" },
];

export function AppHeader() {
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-surface">
      <div className="mx-auto flex h-[72px] w-full max-w-[1600px] items-stretch gap-8 px-4 sm:px-8">
        <Link href="/" className="flex shrink-0 items-center gap-4" aria-label="Perforación IA, ir al panel">
          <Image src="/brand/hochschild-logo.png" alt="Hochschild Mining" width={84} height={42} priority />
          <span className="hidden h-9 w-px bg-line sm:block" aria-hidden />
          <span className="hidden leading-tight sm:block">
            <span className="block font-display text-[1.05rem] font-medium text-ink">Perforación IA</span>
            <span className="block text-[0.78rem] text-ink-3">Reportes de taladros largos</span>
          </span>
        </Link>
        <NavLinks />
        <ApiStatus />
      </div>
      <div className="h-[3px] bg-gold" aria-hidden />
    </header>
  );
}

function NavLinks() {
  const path = usePathname();
  return (
    <nav aria-label="Secciones" className="flex min-w-0 flex-1 items-stretch gap-1 overflow-x-auto">
      {NAV.map((n) => {
        const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
        return (
          <Link
            key={n.href}
            href={n.href}
            aria-current={active ? "page" : undefined}
            className={cx(
              "relative flex shrink-0 items-center px-3 font-display text-[0.95rem] transition-colors",
              active ? "text-ink" : "text-ink-2 hover:text-ink",
            )}
          >
            {n.label}
            <span className={cx("absolute inset-x-3 bottom-0 h-[3px]", active ? "bg-ink" : "bg-transparent")} aria-hidden />
          </Link>
        );
      })}
    </nav>
  );
}

export function HeaderFallback() {
  return <header className="h-[75px] border-b border-line bg-surface" />;
}

function ApiStatus() {
  const { data, error, reload } = useApi<Health>("/api/health");
  useInterval(reload, 8000);
  if (error && !data) {
    return (
      <div className="hidden shrink-0 items-center gap-2 self-center text-sm text-ink-2 lg:flex" title={`API en ${API_URL}`}>
        <Icon name="error" className="text-error" /> API sin conexión
      </div>
    );
  }
  if (!data) return <div className="hidden w-48 lg:block" />;
  return (
    <div className="hidden shrink-0 items-center gap-4 self-center text-right lg:flex">
      {data.busy && (
        <Link href="/modelos" className="flex items-center gap-2 bg-review-tint px-2.5 py-1 text-sm text-ink">
          <Spinner className="size-3.5 text-review" /> Reentrenando
        </Link>
      )}
      <div className="leading-tight">
        <div className="flex items-center justify-end gap-1.5 text-[0.8rem] text-ink-2">
          <span className="size-2 bg-auto" aria-hidden /> Conectado en {data.device.toUpperCase()}
        </div>
        <div className="max-w-56 truncate text-[0.8rem] text-ink-3" title={`Modelo activo ${data.active_model ?? ""} en ${data.workspace}`}>
          {data.active_model ?? "sin modelo activo"}
        </div>
      </div>
    </div>
  );
}
