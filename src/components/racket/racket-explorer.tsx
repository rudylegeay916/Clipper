"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Search, Plus, Check, GitCompareArrows, X } from "lucide-react";
import type { Racket, Forme, Niveau } from "@/types/racket";
import { FORME_LABEL, NIVEAU_LABEL } from "@/data/criteria";
import { RacketCard } from "./racket-card";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type SortKey = "compat" | "prix-asc" | "prix-desc" | "nom";

const FORME_OPTIONS: Forme[] = ["ronde", "goutte", "diamant", "hybride"];
const NIVEAU_OPTIONS: Niveau[] = ["debutant", "loisir", "intermediaire", "confirme", "competition"];

function Select<T extends string>({
  value,
  onChange,
  options,
  placeholder,
}: {
  value: T | "";
  onChange: (v: T | "") => void;
  options: { value: T; label: string }[];
  placeholder: string;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value as T | "")}
      className="h-10 rounded-lg border border-border bg-surface-2 px-3 text-sm text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-volt"
    >
      <option value="">{placeholder}</option>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function RacketExplorer({ rackets, brands }: { rackets: Racket[]; brands: string[] }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [brand, setBrand] = useState<string>("");
  const [forme, setForme] = useState<Forme | "">("");
  const [niveau, setNiveau] = useState<Niveau | "">("");
  const [sort, setSort] = useState<SortKey>("nom");
  const [compare, setCompare] = useState<string[]>([]);

  const filtered = useMemo(() => {
    let list = rackets.filter((r) => {
      if (q && !`${r.marque} ${r.modele}`.toLowerCase().includes(q.toLowerCase())) return false;
      if (brand && r.marque !== brand) return false;
      if (forme && r.specs.forme !== forme) return false;
      if (niveau && !(r.niveauConseille ?? []).includes(niveau)) return false;
      return true;
    });
    list = [...list].sort((a, b) => {
      switch (sort) {
        case "prix-asc":
          return (a.specs.prixIndicatif ?? Infinity) - (b.specs.prixIndicatif ?? Infinity);
        case "prix-desc":
          return (b.specs.prixIndicatif ?? -1) - (a.specs.prixIndicatif ?? -1);
        default:
          return `${a.marque} ${a.modele}`.localeCompare(`${b.marque} ${b.modele}`, "fr");
      }
    });
    return list;
  }, [rackets, q, brand, forme, niveau, sort]);

  function toggleCompare(id: string) {
    setCompare((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : prev.length < 3 ? [...prev, id] : prev,
    );
  }

  return (
    <div>
      {/* Filtres */}
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-surface/60 p-3">
        <div className="relative flex-1 min-w-[180px]">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-2" aria-hidden />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Rechercher une raquette…"
            className="h-10 w-full rounded-lg border border-border bg-surface-2 pl-9 pr-3 text-sm text-fg placeholder:text-muted-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-volt"
          />
        </div>
        <Select value={brand} onChange={setBrand} placeholder="Toutes les marques" options={brands.map((b) => ({ value: b, label: b }))} />
        <Select value={forme} onChange={setForme} placeholder="Toutes les formes" options={FORME_OPTIONS.map((f) => ({ value: f, label: FORME_LABEL[f] }))} />
        <Select value={niveau} onChange={setNiveau} placeholder="Tous les niveaux" options={NIVEAU_OPTIONS.map((n) => ({ value: n, label: NIVEAU_LABEL[n] }))} />
        <Select
          value={sort}
          onChange={(v) => setSort((v || "nom") as SortKey)}
          placeholder="Trier"
          options={[
            { value: "nom" as SortKey, label: "Nom (A→Z)" },
            { value: "prix-asc" as SortKey, label: "Prix croissant" },
            { value: "prix-desc" as SortKey, label: "Prix décroissant" },
          ]}
        />
      </div>

      <p className="mt-4 text-sm text-muted">
        {filtered.length} modèle{filtered.length > 1 ? "s" : ""} · clique sur{" "}
        <Plus className="inline size-3.5" /> pour comparer (jusqu&apos;à 3)
      </p>

      {/* Grille */}
      <div className="mt-4 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
        {filtered.map((r) => {
          const selected = compare.includes(r.id);
          return (
            <div key={r.id} className="relative">
              <button
                type="button"
                onClick={() => toggleCompare(r.id)}
                aria-pressed={selected}
                aria-label={selected ? "Retirer de la comparaison" : "Ajouter à la comparaison"}
                className={cn(
                  "absolute left-3 top-3 z-10 grid size-8 place-items-center rounded-lg border transition-colors",
                  selected
                    ? "border-volt bg-volt text-volt-foreground"
                    : "border-border-strong bg-bg/80 text-muted hover:text-fg",
                )}
              >
                {selected ? <Check className="size-4" /> : <Plus className="size-4" />}
              </button>
              <RacketCard racket={r} />
            </div>
          );
        })}
      </div>

      {filtered.length === 0 && (
        <p className="mt-10 text-center text-muted">Aucune raquette ne correspond à ces filtres.</p>
      )}

      {/* Barre de comparaison */}
      {compare.length > 0 && (
        <div className="fixed inset-x-0 bottom-0 z-40 border-t border-border-strong bg-bg-elevated/95 backdrop-blur-xl">
          <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
            <div className="flex items-center gap-2 text-sm">
              <GitCompareArrows className="size-4 text-volt" />
              <span className="font-medium">{compare.length} sélectionnée{compare.length > 1 ? "s" : ""}</span>
              <button onClick={() => setCompare([])} className="ml-2 inline-flex items-center gap-1 text-muted hover:text-fg">
                <X className="size-3.5" /> vider
              </button>
            </div>
            <Button
              size="sm"
              disabled={compare.length < 2}
              onClick={() => router.push(`/comparateur?ids=${compare.join(",")}`)}
            >
              Comparer
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
