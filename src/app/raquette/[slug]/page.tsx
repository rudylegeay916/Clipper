import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import { ThumbsUp, ThumbsDown, Check, X, ArrowLeft } from "lucide-react";
import { rackets, getRacketBySlug } from "@/data/rackets";
import { getCompromises } from "@/data/compromises";
import {
  NIVEAU_LABEL,
  STYLE_LABEL,
  COTE_LABEL,
  COMMERCIAL_STATUS_LABEL,
} from "@/data/criteria";
import { formatPrice } from "@/lib/utils";
import { RacketImage } from "@/components/racket/racket-image";
import { TechnicalSpecCard } from "@/components/racket/technical-spec-card";
import { ScoreEstimationExplainer } from "@/components/racket/score-estimation-explainer";
import { RacketRadarChart } from "@/components/racket/racket-radar-chart";
import { SourceList } from "@/components/shared/source-list";
import { DataProvenanceBadge } from "@/components/shared/data-provenance-badge";
import { Card, CardBody } from "@/components/ui/card";

export function generateStaticParams() {
  return rackets.map((r) => ({ slug: r.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const racket = getRacketBySlug(slug);
  if (!racket) return { title: "Raquette introuvable · PadelMatch" };
  return {
    title: `${racket.marque} ${racket.modele} · PadelMatch`,
    description: `Caractéristiques sourcées et comportement estimé de la ${racket.marque} ${racket.modele}.`,
  };
}

export default async function RacketDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const racket = getRacketBySlug(slug);
  if (!racket) notFound();

  const compromises = getCompromises(racket);

  return (
    <div className="mx-auto max-w-6xl px-4 py-12 sm:px-6">
      <Link href="/catalogue" className="inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
        <ArrowLeft className="size-4" /> Retour au catalogue
      </Link>

      {/* En-tête */}
      <header className="mt-6 flex flex-wrap items-start justify-between gap-6">
        <div>
          <p className="text-sm uppercase tracking-wide text-muted-2">{racket.marque}</p>
          <h1 className="font-display text-4xl font-bold sm:text-5xl">{racket.modele}</h1>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <DataProvenanceBadge kind={racket.isOfficialData ? "official" : "reseller"} />
            <span className="rounded-md border border-border bg-surface-2 px-2 py-0.5 text-xs text-muted">
              {COMMERCIAL_STATUS_LABEL[racket.commercialStatus]}
            </span>
            {racket.niveauConseille?.map((n) => (
              <span key={n} className="rounded-md border border-border bg-surface-2 px-2 py-0.5 text-xs text-muted">
                {NIVEAU_LABEL[n]}
              </span>
            ))}
            {racket.styleConseille && (
              <span className="rounded-md border border-border bg-surface-2 px-2 py-0.5 text-xs text-muted">
                {STYLE_LABEL[racket.styleConseille]}
              </span>
            )}
          </div>
        </div>
        <div className="text-right">
          <p className="font-display text-3xl font-bold text-volt">{formatPrice(racket.specs.prixIndicatif)}</p>
          <p className="text-xs text-muted-2">Prix indicatif</p>
        </div>
      </header>

      <div className="mt-10 grid gap-6 lg:grid-cols-2">
        <div className="space-y-6">
          <ScoreEstimationExplainer racket={racket} />

          {/* Forts / faibles */}
          {(racket.pointsForts.length > 0 || racket.pointsFaibles.length > 0) && (
            <Card>
              <CardBody className="grid gap-6 sm:grid-cols-2">
                <div>
                  <p className="flex items-center gap-2 font-medium text-fg">
                    <ThumbsUp className="size-4 text-volt" /> Points forts
                  </p>
                  <ul className="mt-3 space-y-1.5">
                    {racket.pointsForts.map((pf) => (
                      <li key={pf} className="flex items-start gap-2 text-sm text-muted">
                        <Check className="mt-0.5 size-3.5 shrink-0 text-volt" /> {pf}
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <p className="flex items-center gap-2 font-medium text-fg">
                    <ThumbsDown className="size-4 text-danger" /> Points faibles
                  </p>
                  <ul className="mt-3 space-y-1.5">
                    {racket.pointsFaibles.map((pf) => (
                      <li key={pf} className="flex items-start gap-2 text-sm text-muted">
                        <X className="mt-0.5 size-3.5 shrink-0 text-danger" /> {pf}
                      </li>
                    ))}
                  </ul>
                </div>
              </CardBody>
            </Card>
          )}

          {/* Compromis */}
          {compromises.length > 0 && (
            <Card>
              <CardBody>
                <p className="font-display text-lg font-semibold">Compromis à accepter</p>
                <ul className="mt-3 space-y-2">
                  {compromises.map((c) => (
                    <li key={c.gain} className="text-sm">
                      <span className="text-volt">+ {c.gain}</span>{" "}
                      <span className="text-muted">— {c.cout}</span>
                    </li>
                  ))}
                </ul>
              </CardBody>
            </Card>
          )}
        </div>

        <div className="space-y-6">
          <RacketImage racket={racket} aspectClass="aspect-[16/10]" className="border border-border" />
          {racket.image?.credit && (
            <p className="-mt-4 text-right text-xs text-muted-2">Image : {racket.image.credit}</p>
          )}

          <Card glass>
            <CardBody>
              <p className="font-display text-lg font-semibold">Profil de jeu</p>
              <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
                <div>
                  <p className="text-muted-2">Style conseillé</p>
                  <p className="font-medium">{racket.styleConseille ? STYLE_LABEL[racket.styleConseille] : "—"}</p>
                </div>
                <div>
                  <p className="text-muted-2">Côté conseillé</p>
                  <p className="font-medium">{racket.coteConseille ? COTE_LABEL[racket.coteConseille] : "—"}</p>
                </div>
              </div>
              <div className="mt-4 rounded-xl border border-border bg-bg/40 p-3">
                <RacketRadarChart rackets={[racket]} height={280} />
              </div>
            </CardBody>
          </Card>

          <TechnicalSpecCard racket={racket} />
          <SourceList racket={racket} />
        </div>
      </div>
    </div>
  );
}
