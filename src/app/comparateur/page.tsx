"use client";

import { Suspense } from "react";
import { useQueryState, parseAsArrayOf, parseAsString } from "nuqs";
import { GitCompareArrows, Plus, Check, ArrowRightLeft } from "lucide-react";
import { rackets, getRacketsByIds } from "@/data/rackets";
import { getCompromises } from "@/data/compromises";
import { RacketRadarChart } from "@/components/racket/racket-radar-chart";
import { RacketComparisonTable } from "@/components/racket/racket-comparison-table";
import { Card, CardBody } from "@/components/ui/card";
import { cn } from "@/lib/utils";

function ComparatorInner() {
  const [ids, setIds] = useQueryState(
    "ids",
    parseAsArrayOf(parseAsString).withDefault([]),
  );

  const selected = getRacketsByIds(ids).slice(0, 3);

  function toggle(id: string) {
    if (ids.includes(id)) {
      setIds(ids.filter((x) => x !== id));
    } else if (selected.length < 3) {
      setIds([...ids, id]);
    }
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-12 sm:px-6">
      <header className="mb-8">
        <h1 className="flex items-center gap-2 font-display text-4xl font-bold">
          <GitCompareArrows className="size-8 text-volt" /> Comparateur
        </h1>
        <p className="mt-2 max-w-2xl text-muted">
          Sélectionne jusqu&apos;à 3 raquettes pour comparer leurs caractéristiques techniques
          et leur comportement estimé, et visualiser les compromis.
        </p>
      </header>

      {/* Sélecteur */}
      <div className="rounded-xl border border-border bg-surface/60 p-4">
        <p className="mb-3 text-sm text-muted">
          {selected.length}/3 sélectionnée{selected.length > 1 ? "s" : ""}
        </p>
        <div className="flex flex-wrap gap-2">
          {rackets.map((r) => {
            const on = ids.includes(r.id);
            const disabled = !on && selected.length >= 3;
            return (
              <button
                key={r.id}
                type="button"
                onClick={() => toggle(r.id)}
                disabled={disabled}
                aria-pressed={on}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm transition-colors",
                  on
                    ? "border-volt bg-volt text-volt-foreground"
                    : "border-border bg-surface-2 text-fg hover:border-border-strong",
                  disabled && "opacity-40",
                )}
              >
                {on ? <Check className="size-3.5" /> : <Plus className="size-3.5" />}
                <span className="text-muted-2">{r.marque}</span> {r.modele}
              </button>
            );
          })}
        </div>
      </div>

      {selected.length < 2 ? (
        <div className="mt-12 flex flex-col items-center justify-center rounded-2xl border border-dashed border-border py-20 text-center">
          <ArrowRightLeft className="size-8 text-muted-2" />
          <p className="mt-4 text-muted">Choisis au moins 2 raquettes ci-dessus pour lancer la comparaison.</p>
        </div>
      ) : (
        <div className="mt-10 space-y-10">
          {/* Radar */}
          <Card glass>
            <CardBody>
              <h2 className="font-display text-xl font-bold">Comportement comparé (estimé)</h2>
              <p className="mt-1 text-sm text-muted">Notes estimées sur 10 — alternative chiffrée dans le tableau ci-dessous.</p>
              <div className="mt-4">
                <RacketRadarChart rackets={selected} height={400} />
              </div>
            </CardBody>
          </Card>

          {/* Tableau */}
          <RacketComparisonTable rackets={selected} />

          {/* Compromis */}
          <div>
            <h2 className="font-display text-xl font-bold">Les compromis de chaque modèle</h2>
            <div className="mt-4 grid gap-4 md:grid-cols-3">
              {selected.map((r) => {
                const comp = getCompromises(r);
                return (
                  <Card key={r.id}>
                    <CardBody>
                      <p className="font-display text-lg font-semibold">{r.modele}</p>
                      <ul className="mt-3 space-y-2">
                        {comp.length === 0 && <li className="text-sm text-muted">Profil équilibré, peu d&apos;arbitrages marqués.</li>}
                        {comp.map((c) => (
                          <li key={c.gain} className="text-sm">
                            <span className="text-volt">+ {c.gain}</span>
                            <br />
                            <span className="text-muted">– {c.cout}</span>
                          </li>
                        ))}
                      </ul>
                    </CardBody>
                  </Card>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function ComparateurPage() {
  return (
    <Suspense>
      <ComparatorInner />
    </Suspense>
  );
}
