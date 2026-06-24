import Link from "next/link";
import { Check, AlertTriangle, Ban, ArrowUpRight, Lightbulb } from "lucide-react";
import type { RecommendationResult as ResultType } from "@/types/scoring";
import { RacketCard } from "@/components/racket/racket-card";
import { RacketRadarChart } from "@/components/racket/racket-radar-chart";
import { CircularGauge } from "@/components/shared/circular-gauge";
import { DataProvenanceBadge } from "@/components/shared/data-provenance-badge";
import { Card, CardBody } from "@/components/ui/card";

const CONF_LABEL = { high: "élevée", medium: "moyenne", low: "réduite" } as const;

export function RecommendationResult({ result }: { result: ResultType }) {
  const [top, ...others] = result.recommendations;

  if (!top) {
    return (
      <Card>
        <CardBody>
          <p className="text-muted">
            Aucune recommandation ne ressort clairement. Élargis tes contraintes (budget, poids)
            et réessaie.
          </p>
        </CardBody>
      </Card>
    );
  }

  return (
    <div className="space-y-12">
      {/* Recommandation principale */}
      <section>
        <h2 className="font-display text-sm font-semibold uppercase tracking-wide text-volt">
          Ta meilleure correspondance
        </h2>
        <Card glass className="mt-4 overflow-hidden">
          <CardBody>
            <div className="grid gap-8 lg:grid-cols-[1fr_320px]">
              <div>
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <p className="text-sm uppercase tracking-wide text-muted-2">{top.racket.marque}</p>
                    <h3 className="font-display text-3xl font-bold">{top.racket.modele}</h3>
                    <div className="mt-2 flex items-center gap-2">
                      <DataProvenanceBadge kind={top.racket.isOfficialData ? "official" : "reseller"} />
                      <span className="text-xs text-muted">
                        Confiance de la reco : {CONF_LABEL[top.matchConfidence]}
                      </span>
                    </div>
                  </div>
                  <CircularGauge value={top.scoreGlobal} label="compatibilité" size={120} />
                </div>

                {/* Raisons */}
                {top.reasons.length > 0 && (
                  <ul className="mt-6 space-y-2">
                    {top.reasons.map((r) => (
                      <li key={r.rule + r.message} className="flex items-start gap-2 text-sm">
                        <Check className="mt-0.5 size-4 shrink-0 text-volt" aria-hidden />
                        <span className="text-fg">{r.message}</span>
                      </li>
                    ))}
                  </ul>
                )}

                {/* Vigilance */}
                {top.warnings.filter((w) => w.tone !== "neutral").length > 0 && (
                  <div className="mt-5 rounded-lg border border-warning/30 bg-warning/5 p-3">
                    <p className="flex items-center gap-2 text-sm font-medium text-warning">
                      <AlertTriangle className="size-4" /> Points de vigilance
                    </p>
                    <ul className="mt-2 space-y-1.5">
                      {top.warnings
                        .filter((w) => w.tone !== "neutral")
                        .map((w) => (
                          <li key={w.rule + w.message} className="text-sm text-muted">
                            {w.message}
                          </li>
                        ))}
                    </ul>
                  </div>
                )}

                <Link
                  href={`/raquette/${top.racket.slug}`}
                  className="mt-6 inline-flex items-center gap-1 text-sm font-medium text-volt hover:underline"
                >
                  Voir la fiche complète <ArrowUpRight className="size-4" />
                </Link>
              </div>

              <div className="rounded-xl border border-border bg-bg/40 p-3">
                <RacketRadarChart rackets={[top.racket]} height={300} />
              </div>
            </div>
          </CardBody>
        </Card>
      </section>

      {/* Autres recommandations */}
      {others.length > 0 && (
        <section>
          <h2 className="font-display text-xl font-bold">Autres bons choix pour toi</h2>
          <div className="mt-4 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {others.map((rec) => (
              <RacketCard key={rec.racket.id} racket={rec.racket} scoreGlobal={rec.scoreGlobal} />
            ))}
          </div>
        </section>
      )}

      {/* À éviter */}
      {result.toAvoid.length > 0 && (
        <section>
          <h2 className="flex items-center gap-2 font-display text-xl font-bold">
            <Ban className="size-5 text-danger" /> Plutôt à éviter pour ton profil
          </h2>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            {result.toAvoid.map((a) => (
              <Card key={a.racket.id} className="border-danger/20">
                <CardBody>
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-xs uppercase tracking-wide text-muted-2">{a.racket.marque}</p>
                      <Link href={`/raquette/${a.racket.slug}`} className="font-display text-lg font-semibold hover:text-volt">
                        {a.racket.modele}
                      </Link>
                    </div>
                  </div>
                  <ul className="mt-3 space-y-1.5">
                    {a.reasons.map((r) => (
                      <li key={r.message} className="flex items-start gap-2 text-sm text-muted">
                        <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-danger" aria-hidden />
                        {r.message}
                      </li>
                    ))}
                  </ul>
                </CardBody>
              </Card>
            ))}
          </div>
        </section>
      )}

      {/* Conseils */}
      <section>
        <Card>
          <CardBody>
            <h2 className="flex items-center gap-2 font-display text-xl font-bold">
              <Lightbulb className="size-5 text-volt" /> Avant d&apos;acheter
            </h2>
            <div className="mt-4 grid gap-4 text-sm text-muted sm:grid-cols-3">
              <div>
                <p className="font-medium text-fg">Teste avant de trancher</p>
                <p className="mt-1">
                  Si possible, essaie la raquette (test club, ami) : les sensations priment sur les chiffres.
                </p>
              </div>
              <div>
                <p className="font-medium text-fg">Progresse sans te brûler</p>
                <p className="mt-1">
                  Évite de viser une raquette trop exigeante « pour progresser » : une raquette
                  tolérante accélère souvent les progrès.
                </p>
              </div>
              <div>
                <p className="font-medium text-fg">Le confort n&apos;est pas un luxe</p>
                <p className="mt-1">
                  En cas de gêne au bras, privilégie un noyau souple et un bon sweet spot : tu joueras
                  plus longtemps, plus souvent.
                </p>
              </div>
            </div>
          </CardBody>
        </Card>
      </section>
    </div>
  );
}
