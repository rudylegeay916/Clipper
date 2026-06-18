import { Sparkles } from "lucide-react";
import type { Racket } from "@/types/racket";
import { estimationRationale } from "@/lib/estimate-scores";
import { CRITERIA } from "@/data/criteria";
import { ScoreBar } from "@/components/shared/score-bar";
import { Card, CardBody, CardTitle } from "@/components/ui/card";

/* Affiche les 7 scores estimés + explique COMMENT ils sont estimés.
   Transparence : ces notes ne sont pas des données officielles fabricant. */
export function ScoreEstimationExplainer({ racket }: { racket: Racket }) {
  const rationale = estimationRationale(racket.specs);

  return (
    <Card>
      <CardBody>
        <div className="flex items-center justify-between gap-2">
          <CardTitle>Comportement en jeu</CardTitle>
          <span className="inline-flex items-center gap-1 rounded-md border border-provenance-estimated/40 bg-provenance-estimated/10 px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-provenance-estimated">
            <Sparkles className="size-3" aria-hidden />
            Estimation experte
          </span>
        </div>

        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {CRITERIA.map((c) => (
            <ScoreBar
              key={c.key}
              label={c.label}
              value={racket.estimatedScores[c.key]}
            />
          ))}
        </div>

        <details className="mt-5 rounded-lg border border-border bg-surface-2/60 p-4">
          <summary className="cursor-pointer text-sm font-medium text-fg">
            Comment ces notes sont-elles estimées ?
          </summary>
          <p className="mt-3 text-sm text-muted">
            Ces notes sont des <strong className="text-fg">estimations</strong> calculées par
            notre moteur ({racket.analysisVersion}) à partir des caractéristiques techniques
            sourcées ci-contre. Elles ne proviennent pas du fabricant.
          </p>
          {rationale.length > 0 && (
            <ul className="mt-3 space-y-1.5 text-sm text-muted">
              {rationale.map((r) => (
                <li key={r} className="flex gap-2">
                  <span className="text-volt">•</span>
                  {r}
                </li>
              ))}
            </ul>
          )}
        </details>
      </CardBody>
    </Card>
  );
}
