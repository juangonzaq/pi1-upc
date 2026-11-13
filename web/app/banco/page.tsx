import type { Metadata } from "next";
import { Bench } from "./bench";

export const metadata: Metadata = { title: "Banco de recortes" };

export default function Page() {
  return <Bench />;
}
