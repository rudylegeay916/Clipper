import type { PlayerProfile } from "@/types/profile";
import type { IdealTarget } from "@/types/scoring";
import type { ScoreKey } from "@/types/racket";
import { SCORE_KEYS } from "@/types/racket";
import {
  FORME_LABEL,
  EQUILIBRE_LABEL,
  NOYAU_LABEL,
  RIGIDITE_LABEL,
  NIVEAU_LABEL,
  STYLE_LABEL,
  CRITERIA_BY_KEY,
} from "@/data/criteria";
import { Card, CardBody } from "@/components/ui/card";

function topCriteria(target: IdealTarget, n = 3): ScoreKey[] {
  return [...SCORE_KEYS].sort((a, b) => target.weights[b] - target.weights[a]).slice(0, n);
}

export function ResultSummary({
  profile,
  target,
}: {
  profile: PlayerProfile;
  target: IdealTarget;
}) {
  const poids =
    target.poidsCibleMin != null && target.poidsCibleMax != null
      ? `${target.poidsCibleMin}–${target.poidsCibleMax} g`
      : "—";

  const idealSpecs = [
    { label: "Forme", value: target.formesPreferees.map((f) => FORME_LABEL[f]).slice(0, 2).join(" / ") },
    { label: "Équilibre", value: target.equilibresPreferes.map((e) => EQUILIBRE_LABEL[e]).slice(0, 2).join(" / ") },
    { label: "Poids conseillé", value: poids },
    { label: "Noyau", value: target.noyauxPreferes.map((nx) => NOYAU_LABEL[nx]).slice(0, 2).join(" / ") },
    { label: "Rigidité", value: target.rigiditeCible ? RIGIDITE_LABEL[target.rigiditeCible] : "—" },
  ];

  const prios = topCriteria(target);

  return (
    <Card glass>
      <CardBody>
        <div className="grid gap-6 md:grid-cols-2">
          <div>
            <h3 className="font-display text-sm font-semibold uppercase tracking-wide text-muted">
              Ton profil
            </h3>
            <p className="mt-3 text-lg leading-relaxed">
              Joueur <strong className="text-volt">{NIVEAU_LABEL[profile.niveau].toLowerCase()}</strong>,{" "}
              style <strong className="text-volt">{STYLE_LABEL[profile.style].toLowerCase()}</strong>
              {profile.position !== "inconnu" && (
                <>
                  , côté <strong className="text-volt">{profile.position}</strong>
                </>
              )}
              {(profile.prioriteConfort || profile.douleurs.some((d) => d !== "aucune")) && (
                <>
                  , avec une <strong className="text-volt">recherche de confort</strong>
                </>
              )}
              .
            </p>
            <div className="mt-4">
              <p className="text-sm text-muted">Tes critères prioritaires :</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {prios.map((k) => (
                  <span key={k} className="rounded-full border border-volt/40 bg-volt/10 px-3 py-1 text-sm text-volt">
                    {CRITERIA_BY_KEY[k].label}
                  </span>
                ))}
              </div>
            </div>
          </div>

          <div>
            <h3 className="font-display text-sm font-semibold uppercase tracking-wide text-muted">
              Type de raquette conseillé
            </h3>
            <dl className="mt-3 divide-y divide-border">
              {idealSpecs.map((s) => (
                <div key={s.label} className="flex items-center justify-between py-2">
                  <dt className="text-sm text-muted">{s.label}</dt>
                  <dd className="text-sm font-medium text-fg">{s.value}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}
