import Link from "next/link";
import {
  Target,
  Gauge,
  ShieldCheck,
  Sparkles,
  ArrowRight,
  Compass,
  AlertCircle,
} from "lucide-react";
import { ButtonLink } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { BallTrajectoryBackground } from "@/components/shared/ball-trajectory-background";
import { RacketCard } from "@/components/racket/racket-card";
import { CRITERIA } from "@/data/criteria";
import { rackets, brands } from "@/data/rackets";
import { recommend } from "@/lib/scoring/engine";
import { defaultProfile } from "@/types/profile";

// Exemple de recommandation : profil loisir cherchant du confort.
const exampleProfile = {
  ...defaultProfile,
  niveau: "loisir" as const,
  force: "moyenne" as const,
  prioriteConfort: true,
  position: "droit" as const,
  style: "defensif" as const,
};
const exampleResult = recommend(exampleProfile);

const TECH = [
  { label: "Forme", value: "Ronde · Goutte · Diamant · Hybride" },
  { label: "Équilibre", value: "Bas · Moyen · Haut" },
  { label: "Noyau", value: "EVA soft → hard · Multi-EVA · Foam" },
  { label: "Matériaux", value: "Fibre de verre · Carbone 3K→18K · Graphène" },
];

export default function Home() {
  return (
    <>
      {/* HERO */}
      <section className="relative overflow-hidden border-b border-border">
        <BallTrajectoryBackground />
        <div className="absolute inset-0 bg-gradient-to-b from-transparent via-transparent to-bg" />
        <div className="relative mx-auto max-w-7xl px-4 py-20 sm:px-6 sm:py-28">
          <div className="max-w-3xl">
            <span className="inline-flex items-center gap-2 rounded-full border border-border-strong bg-surface/60 px-3 py-1 text-xs text-muted">
              <ShieldCheck className="size-3.5 text-volt" />
              {rackets.length} modèles réels · {brands.length} marques · données sourcées
            </span>
            <h1 className="mt-6 font-display text-5xl font-bold leading-[1.05] tracking-tight sm:text-7xl">
              Trouve la raquette de padel <span className="text-volt">vraiment adaptée</span> à ton jeu
            </h1>
            <p className="mt-6 max-w-2xl text-lg text-muted">
              Trop de joueurs achètent une raquette trop lourde, trop dure ou trop exigeante —
              et stagnent ou se blessent. On t&apos;aide à comprendre ce qui te correspond, et pourquoi.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <ButtonLink href="/questionnaire" size="lg">
                <Target className="size-5" /> Trouver ma raquette
              </ButtonLink>
              <ButtonLink href="/comparateur" size="lg" variant="secondary">
                <Gauge className="size-5" /> Comparer les modèles
              </ButtonLink>
            </div>
          </div>
        </div>
      </section>

      {/* PROBLÈME */}
      <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
        <div className="grid gap-4 sm:grid-cols-3">
          {[
            { t: "Trop puissante ≠ meilleure", d: "Une raquette diamant très puissante devient ingérable si tu manques de régularité. Plus de puissance = souvent moins de tolérance." },
            { t: "Le confort fait progresser", d: "Une raquette confortable et tolérante te fait jouer plus longtemps, plus souvent, et progresser plus vite — surtout en cas de douleurs." },
            { t: "Chaque profil est différent", d: "Côté droit ou gauche, offensif ou défensif, débutant ou compétiteur : les bons critères ne sont jamais les mêmes." },
          ].map((b) => (
            <Card key={b.t} className="border-border">
              <CardBody>
                <AlertCircle className="size-5 text-volt" />
                <h3 className="mt-3 font-display text-lg font-semibold">{b.t}</h3>
                <p className="mt-2 text-sm text-muted">{b.d}</p>
              </CardBody>
            </Card>
          ))}
        </div>
      </section>

      {/* 7 CRITÈRES */}
      <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
        <div>
          <h2 className="font-display text-3xl font-bold sm:text-4xl">Les 7 critères de comportement</h2>
          <p className="mt-2 max-w-2xl text-muted">
            On évalue chaque raquette sur 7 dimensions de jeu — des estimations expertes,
            calculées à partir des caractéristiques techniques sourcées.
          </p>
        </div>
        <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {CRITERIA.map((c) => (
            <Card key={c.key} glass>
              <CardBody>
                <h3 className="font-display text-lg font-semibold text-volt">{c.label}</h3>
                <p className="mt-2 text-sm text-muted">{c.description}</p>
              </CardBody>
            </Card>
          ))}
        </div>
      </section>

      {/* CARACTÉRISTIQUES TECHNIQUES */}
      <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
        <div className="grid gap-10 lg:grid-cols-2">
          <div>
            <h2 className="font-display text-3xl font-bold sm:text-4xl">Des caractéristiques sourcées, jamais inventées</h2>
            <p className="mt-4 text-muted">
              Forme, équilibre, poids, noyau, surface, matériaux : chaque donnée technique provient
              d&apos;une fiche officielle ou d&apos;un revendeur sérieux. Quand une info n&apos;est pas
              communiquée, on l&apos;indique clairement plutôt que de la deviner.
            </p>
            <Link href="/guide" className="mt-6 inline-flex items-center gap-1 text-sm font-medium text-volt hover:underline">
              Comprendre les caractéristiques <ArrowRight className="size-4" />
            </Link>
          </div>
          <dl className="grid gap-3">
            {TECH.map((t) => (
              <div key={t.label} className="flex items-center justify-between rounded-xl border border-border bg-surface/60 px-5 py-4">
                <dt className="font-medium text-fg">{t.label}</dt>
                <dd className="text-right text-sm text-muted">{t.value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      {/* QUESTIONNAIRE + EXEMPLE */}
      <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
        <div className="grid items-center gap-10 lg:grid-cols-2">
          <div>
            <span className="inline-flex items-center gap-2 text-sm text-volt">
              <Compass className="size-4" /> Questionnaire de profil
            </span>
            <h2 className="mt-3 font-display text-3xl font-bold sm:text-4xl">
              6 étapes pour révéler ton profil joueur
            </h2>
            <p className="mt-4 text-muted">
              Physique, niveau, style, position, priorités, contraintes : on traduit tes réponses
              en une recommandation justifiée, avec score de compatibilité, radar et points de vigilance.
            </p>
            <ButtonLink href="/questionnaire" size="lg" className="mt-6">
              <Sparkles className="size-5" /> Commencer le questionnaire
            </ButtonLink>
          </div>

          <div>
            <p className="mb-3 text-sm text-muted">
              Exemple — profil loisir, défensif, recherche de confort :
            </p>
            {exampleResult.recommendations[0] && (
              <RacketCard
                racket={exampleResult.recommendations[0].racket}
                scoreGlobal={exampleResult.recommendations[0].scoreGlobal}
              />
            )}
          </div>
        </div>
      </section>
    </>
  );
}
