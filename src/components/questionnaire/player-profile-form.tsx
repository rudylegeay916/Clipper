"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  type PlayerProfile,
  type Force,
  type Douleur,
  type Exigence,
  type Erreur,
  type Position,
  defaultProfile,
} from "@/types/profile";
import type { Niveau, StyleJeu, Rigidite } from "@/types/racket";
import { CRITERIA, NIVEAU_LABEL, STYLE_LABEL, RIGIDITE_LABEL } from "@/data/criteria";
import { QuestionStep, OptionCard, Chip } from "./question-step";

const TOTAL = 6;

const FORCE_OPTS: { v: Force; label: string; desc: string }[] = [
  { v: "faible", label: "Plutôt faible", desc: "Je privilégie la facilité" },
  { v: "moyenne", label: "Moyenne", desc: "Condition physique correcte" },
  { v: "forte", label: "Forte", desc: "Bonne explosivité, j'encaisse le poids" },
];
const DOULEUR_OPTS: { v: Douleur; label: string }[] = [
  { v: "bras", label: "Bras" },
  { v: "poignet", label: "Poignet" },
  { v: "epaule", label: "Épaule" },
  { v: "coude", label: "Coude" },
  { v: "aucune", label: "Aucune douleur" },
];
const NIVEAU_OPTS: Niveau[] = ["debutant", "loisir", "intermediaire", "confirme", "competition"];
const CLASSEMENTS = ["P25", "P100", "P250", "P500", "P1000", "P2000"];
const STYLE_OPTS: { v: StyleJeu; desc: string }[] = [
  { v: "offensif", desc: "J'attaque, je cherche à conclure" },
  { v: "equilibre", desc: "Je m'adapte au point" },
  { v: "defensif", desc: "Je défends, je construis, je fais durer" },
];
const POSITION_OPTS: { v: Position; label: string }[] = [
  { v: "droit", label: "Côté droit" },
  { v: "gauche", label: "Côté gauche" },
  { v: "polyvalent", label: "Les deux côtés" },
  { v: "inconnu", label: "Je ne sais pas encore" },
];
const EXIGENCE_OPTS: { v: Exigence; label: string; desc: string }[] = [
  { v: "facile", label: "Facile", desc: "Tolérante, accessible" },
  { v: "progressive", label: "Évolutive", desc: "M'accompagne dans ma progression" },
  { v: "exigeante", label: "Exigeante", desc: "Performance avant tout" },
];
const SENSATION_OPTS: Rigidite[] = ["soft", "medium", "hard"];
const ERREUR_OPTS: { v: Erreur; label: string }[] = [
  { v: "trop_lourde", label: "Trop lourde" },
  { v: "trop_dure", label: "Trop dure" },
  { v: "pas_assez_puissante", label: "Pas assez puissante" },
  { v: "trop_exigeante", label: "Trop exigeante" },
  { v: "manque_controle", label: "Manque de contrôle" },
  { v: "douleurs", label: "M'a causé des douleurs" },
];

function NumberField({
  label,
  value,
  onChange,
  unit,
  placeholder,
}: {
  label: string;
  value: number | null | undefined;
  onChange: (v: number | null) => void;
  unit?: string;
  placeholder?: string;
}) {
  return (
    <label className="block">
      <span className="text-sm text-muted">{label}</span>
      <div className="mt-1 flex items-center gap-2">
        <input
          type="number"
          inputMode="numeric"
          value={value ?? ""}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
          className="h-11 w-full rounded-lg border border-border bg-surface-2 px-3 text-fg placeholder:text-muted-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-volt"
        />
        {unit && <span className="text-sm text-muted-2">{unit}</span>}
      </div>
    </label>
  );
}

export function PlayerProfileForm({ brands }: { brands: string[] }) {
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [p, setP] = useState<PlayerProfile>(defaultProfile);

  const set = <K extends keyof PlayerProfile>(key: K, value: PlayerProfile[K]) =>
    setP((prev) => ({ ...prev, [key]: value }));

  const toggleDouleur = (d: Douleur) => {
    setP((prev) => {
      if (d === "aucune") return { ...prev, douleurs: ["aucune"] };
      const without = prev.douleurs.filter((x) => x !== "aucune");
      const next = without.includes(d) ? without.filter((x) => x !== d) : [...without, d];
      return { ...prev, douleurs: next };
    });
  };

  const toggleErreur = (e: Erreur) =>
    setP((prev) => ({
      ...prev,
      erreurs: prev.erreurs.includes(e)
        ? prev.erreurs.filter((x) => x !== e)
        : [...prev.erreurs, e],
    }));

  const next = () => setStep((s) => Math.min(TOTAL, s + 1));
  const back = () => setStep((s) => Math.max(1, s - 1));

  const submit = () => {
    const encoded = encodeURIComponent(JSON.stringify(p));
    router.push(`/resultats?p=${encoded}`);
  };

  return (
    <>
      {step === 1 && (
        <QuestionStep
          step={1}
          total={TOTAL}
          title="Ton profil physique"
          subtitle="Pour adapter le poids, le confort et la maniabilité à ton corps et tes sensations."
          onNext={next}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <NumberField label="Taille" value={p.taille} onChange={(v) => set("taille", v)} unit="cm" placeholder="175" />
            <NumberField label="Poids" value={p.poids} onChange={(v) => set("poids", v)} unit="kg" placeholder="72" />
          </div>

          <p className="mt-6 text-sm font-medium text-fg">Force physique ressentie</p>
          <div className="mt-2 grid gap-3 sm:grid-cols-3">
            {FORCE_OPTS.map((o) => (
              <OptionCard key={o.v} selected={p.force === o.v} onClick={() => set("force", o.v)} title={o.label} description={o.desc} />
            ))}
          </div>

          <p className="mt-6 text-sm font-medium text-fg">Douleurs éventuelles</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {DOULEUR_OPTS.map((o) => (
              <Chip key={o.v} selected={p.douleurs.includes(o.v)} onClick={() => toggleDouleur(o.v)}>
                {o.label}
              </Chip>
            ))}
          </div>

          <div className="mt-6 flex flex-wrap gap-2">
            <Chip selected={p.prioriteConfort} onClick={() => set("prioriteConfort", !p.prioriteConfort)}>
              Je privilégie le confort
            </Chip>
          </div>
        </QuestionStep>
      )}

      {step === 2 && (
        <QuestionStep step={2} total={TOTAL} title="Ton niveau de jeu" onBack={back} onNext={next}>
          <div className="grid gap-3 sm:grid-cols-2">
            {NIVEAU_OPTS.map((n) => (
              <OptionCard key={n} selected={p.niveau === n} onClick={() => set("niveau", n)} title={NIVEAU_LABEL[n]} />
            ))}
          </div>
          <p className="mt-6 text-sm font-medium text-fg">Classement (optionnel)</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {CLASSEMENTS.map((c) => (
              <Chip key={c} selected={p.classement === c} onClick={() => set("classement", p.classement === c ? null : c)}>
                {c}
              </Chip>
            ))}
          </div>
        </QuestionStep>
      )}

      {step === 3 && (
        <QuestionStep step={3} total={TOTAL} title="Ton style de jeu" onBack={back} onNext={next}>
          <div className="grid gap-3">
            {STYLE_OPTS.map((o) => (
              <OptionCard key={o.v} selected={p.style === o.v} onClick={() => set("style", o.v)} title={STYLE_LABEL[o.v]} description={o.desc} />
            ))}
          </div>
          <div className="mt-6 flex flex-wrap gap-2">
            <Chip selected={!!p.monteAuFilet} onClick={() => set("monteAuFilet", !p.monteAuFilet)}>
              Je monte beaucoup au filet
            </Chip>
            <Chip selected={!!p.construitPoint} onClick={() => set("construitPoint", !p.construitPoint)}>
              Je préfère construire le point
            </Chip>
          </div>
        </QuestionStep>
      )}

      {step === 4 && (
        <QuestionStep step={4} total={TOTAL} title="Ta position sur le terrain" subtitle="Le côté influence les besoins en contrôle, puissance et prise d'effet." onBack={back} onNext={next}>
          <div className="grid gap-3 sm:grid-cols-2">
            {POSITION_OPTS.map((o) => (
              <OptionCard key={o.v} selected={p.position === o.v} onClick={() => set("position", o.v)} title={o.label} />
            ))}
          </div>
        </QuestionStep>
      )}

      {step === 5 && (
        <QuestionStep step={5} total={TOTAL} title="Tes priorités" subtitle="Dis-nous ce qui compte le plus pour toi (0 = peu important, 3 = essentiel)." onBack={back} onNext={next}>
          <div className="space-y-5">
            {CRITERIA.map((c) => (
              <div key={c.key}>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-fg">{c.label}</span>
                  <span className="font-mono text-sm text-volt tabular-nums">{p.preferences[c.key]}</span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={3}
                  step={1}
                  value={p.preferences[c.key]}
                  onChange={(e) =>
                    set("preferences", { ...p.preferences, [c.key]: Number(e.target.value) })
                  }
                  className="mt-2 w-full accent-[var(--volt)]"
                  aria-label={c.label}
                />
              </div>
            ))}
          </div>
        </QuestionStep>
      )}

      {step === 6 && (
        <QuestionStep step={6} total={TOTAL} title="Contraintes & expérience" onBack={back} onNext={submit} isLast>
          <div className="grid gap-4 sm:grid-cols-2">
            <NumberField label="Budget maximum" value={p.budgetMax} onChange={(v) => set("budgetMax", v)} unit="€" placeholder="250" />
            <NumberField label="Poids maximum souhaité" value={p.poidsMax} onChange={(v) => set("poidsMax", v)} unit="g" placeholder="365" />
          </div>

          <p className="mt-6 text-sm font-medium text-fg">Type de raquette souhaité</p>
          <div className="mt-2 grid gap-3 sm:grid-cols-3">
            {EXIGENCE_OPTS.map((o) => (
              <OptionCard key={o.v} selected={p.exigence === o.v} onClick={() => set("exigence", o.v)} title={o.label} description={o.desc} />
            ))}
          </div>

          <p className="mt-6 text-sm font-medium text-fg">Sensation souhaitée</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {SENSATION_OPTS.map((s) => (
              <Chip key={s} selected={p.sensation === s} onClick={() => set("sensation", s)}>
                {RIGIDITE_LABEL[s]}
              </Chip>
            ))}
            <Chip selected={p.evolutive} onClick={() => set("evolutive", !p.evolutive)}>
              Raquette évolutive
            </Chip>
          </div>

          <p className="mt-6 text-sm font-medium text-fg">Marque préférée (optionnel)</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {brands.map((b) => (
              <Chip key={b} selected={p.marquePreferee === b} onClick={() => set("marquePreferee", p.marquePreferee === b ? null : b)}>
                {b}
              </Chip>
            ))}
          </div>

          <p className="mt-6 text-sm font-medium text-fg">Erreurs déjà faites (optionnel)</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {ERREUR_OPTS.map((o) => (
              <Chip key={o.v} selected={p.erreurs.includes(o.v)} onClick={() => toggleErreur(o.v)}>
                {o.label}
              </Chip>
            ))}
          </div>
        </QuestionStep>
      )}
    </>
  );
}
