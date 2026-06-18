import type { Metadata } from "next";
import { rackets, brands } from "@/data/rackets";
import { RacketExplorer } from "@/components/racket/racket-explorer";

export const metadata: Metadata = {
  title: "Catalogue des raquettes · PadelMatch",
  description:
    "Toutes les raquettes de padel réelles, sourcées : filtre par marque, forme, niveau et prix, et compare jusqu'à 3 modèles.",
};

export default function CataloguePage() {
  return (
    <div className="mx-auto max-w-7xl px-4 py-12 sm:px-6">
      <header className="mb-8">
        <h1 className="font-display text-4xl font-bold">Catalogue</h1>
        <p className="mt-2 max-w-2xl text-muted">
          {rackets.length} modèles réels de {brands.length} marques. Caractéristiques techniques
          sourcées, notes de comportement estimées.
        </p>
      </header>
      <RacketExplorer rackets={rackets} brands={brands} />
    </div>
  );
}
