import type { Metadata } from "next";
import { EvolutionView } from "./evolution";

export const metadata: Metadata = { title: "Evolución del modelo" };

export default function Page() {
  return <EvolutionView />;
}
