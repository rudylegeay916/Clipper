import type { Metadata } from "next";
import { ButtonLink } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { CRITERIA } from "@/data/criteria";

export const metadata: Metadata = {
  title: "Guide du matériel · PadelMatch",
  description:
    "Comprendre les formes, l'équilibre, le poids, les noyaux, les matériaux, la rigidité et le sweet spot d'une raquette de padel.",
};

const SECTIONS: { title: string; body: string }[] = [
  {
    title: "Les formes",
    body: "Ronde : sweet spot centré, très tolérante et maniable — idéale pour débuter ou jouer côté droit. Goutte d'eau : compromis polyvalent puissance/contrôle. Diamant : sweet spot haut placé, puissante mais exigeante et peu tolérante. Hybride : entre goutte et diamant.",
  },
  {
    title: "L'équilibre",
    body: "Bas (head-light) : plus maniable, plus de contrôle, moins fatigant. Haut (head-heavy) : plus de puissance et d'effet de levier au smash, mais plus fatigant et moins maniable. Moyen : polyvalent.",
  },
  {
    title: "Le poids",
    body: "Un poids élevé (370 g+) apporte de la stabilité face aux balles lourdes mais réduit la maniabilité et fatigue davantage. Un poids contenu (350–360 g) privilégie la vivacité. À adapter à ta force physique et à d'éventuelles douleurs.",
  },
  {
    title: "Les noyaux (gomme)",
    body: "EVA soft : confort et contrôle, idéal bras sensibles. EVA medium : équilibre confort/réponse. EVA hard : puissance et précision mais plus dur pour le bras. Multi-EVA : densités combinées (confort + explosivité). Foam : très souple et confortable, sortie de balle facile.",
  },
  {
    title: "Les matériaux",
    body: "Fibre de verre : souple, confortable, accessible. Carbone (3K, 12K, 18K) : plus rigide et précis à mesure que le tissage est fin, mais plus exigeant. Carbone aluminisé / graphène : rigidité et accroche supplémentaires.",
  },
  {
    title: "La rigidité",
    body: "Plus une raquette est rigide, plus elle est précise et réactive — mais moins elle est confortable et plus elle transmet de vibrations. Un cadre souple protège le bras et aide au contrôle.",
  },
  {
    title: "Le sweet spot",
    body: "C'est la zone optimale de frappe. Large = plus de tolérance sur les balles décentrées (idéal progression). Réduit = plus de précision pour les joueurs réguliers, mais moins pardonnant.",
  },
  {
    title: "Puissance vs sortie de balle",
    body: "La puissance est le potentiel d'accélération sur les frappes appuyées. La sortie de balle est le rendement à faible énergie : une raquette peut bien sortir la balle sans être « puissante » au sens explosif.",
  },
  {
    title: "Contrôle vs tolérance",
    body: "Le contrôle, c'est la précision quand tu frappes bien. La tolérance, c'est ce que la raquette pardonne quand tu frappes mal. Un débutant a surtout besoin de tolérance ; un joueur régulier recherche le contrôle.",
  },
  {
    title: "Pourquoi le confort compte",
    body: "Le padel sollicite coude et épaule. Une raquette inconfortable peut causer des douleurs (épicondylite) et te faire jouer moins. Le confort n'est pas un luxe : c'est ce qui te permet de progresser dans la durée.",
  },
  {
    title: "Comment choisir selon son niveau",
    body: "Débutant/loisir : ronde ou goutte, équilibre bas/moyen, noyau souple, poids modéré, grand sweet spot. Intermédiaire : goutte polyvalente évolutive. Confirmé/compétition : selon le style et le côté, on peut viser diamant/équilibre haut — à condition d'avoir le niveau et la condition physique.",
  },
];

export default function GuidePage() {
  return (
    <div className="mx-auto max-w-4xl px-4 py-12 sm:px-6">
      <header className="mb-10">
        <h1 className="font-display text-4xl font-bold sm:text-5xl">Guide du matériel</h1>
        <p className="mt-3 text-lg text-muted">
          Tout ce qu&apos;il faut comprendre pour choisir une raquette — même si tu débutes
          totalement dans le matériel.
        </p>
      </header>

      {/* Rappel des 7 critères */}
      <Card glass className="mb-10">
        <CardBody>
          <h2 className="font-display text-xl font-bold">Les 7 critères de comportement</h2>
          <dl className="mt-4 grid gap-x-8 gap-y-3 sm:grid-cols-2">
            {CRITERIA.map((c) => (
              <div key={c.key}>
                <dt className="font-medium text-volt">{c.label}</dt>
                <dd className="text-sm text-muted">{c.description}</dd>
              </div>
            ))}
          </dl>
        </CardBody>
      </Card>

      <div className="space-y-8">
        {SECTIONS.map((s) => (
          <section key={s.title} className="border-l-2 border-volt/40 pl-5">
            <h2 className="font-display text-2xl font-semibold">{s.title}</h2>
            <p className="mt-2 leading-relaxed text-muted">{s.body}</p>
          </section>
        ))}
      </div>

      <div className="mt-12 rounded-2xl border border-border bg-surface/60 p-8 text-center">
        <h2 className="font-display text-2xl font-bold">Prêt à trouver ta raquette ?</h2>
        <p className="mt-2 text-muted">Réponds au questionnaire : on traduit tout ça en recommandation personnalisée.</p>
        <ButtonLink href="/questionnaire" size="lg" className="mt-5">
          Trouver ma raquette
        </ButtonLink>
      </div>
    </div>
  );
}
