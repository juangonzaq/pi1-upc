import type { Metadata } from "next";
import { Outfit } from "next/font/google";
import { Suspense } from "react";
import { AppHeader, HeaderFallback } from "@/components/app-header";
import "./globals.css";

// Outfit: grotesca geométrica libre, cercana a la Galano Grotesque de la marca (títulos, navegación y cifras)
const outfit = Outfit({ variable: "--font-outfit", subsets: ["latin"], weight: ["400", "500", "600"] });

export const metadata: Metadata = {
  title: { default: "Perforación IA, Hochschild Mining", template: "%s | Perforación IA" },
  description: "Reconocimiento con TrOCR, revisión manual y reentrenamiento de los reportes de perforación de taladros largos.",
  icons: { icon: "/brand/favicon.ico" },
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="es" className={`${outfit.variable} h-full antialiased`}>
      <body className="min-h-full bg-paper text-ink">
        <Suspense fallback={<HeaderFallback />}>
          <AppHeader />
        </Suspense>
        <main className="mx-auto w-full max-w-[1600px] px-4 py-8 sm:px-8 sm:py-10">{children}</main>
      </body>
    </html>
  );
}
