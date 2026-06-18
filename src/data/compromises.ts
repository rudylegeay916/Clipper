import type { Racket } from "@/types/racket";

/* =========================================================================
   Compromis pédagogiques — règles générales du padel
   Sert à expliciter les arbitrages d'un modèle (comparateur, fiche).
   ========================================================================= */

export interface Compromise {
  gain: string;
  cout: string;
}

export function getCompromises(racket: Racket): Compromise[] {
  const out: Compromise[] = [];
  const s = racket.specs;

  if (s.forme === "diamant") {
    out.push({ gain: "Plus de puissance et d'agressivité", cout: "Moins de tolérance, plus exigeante" });
  }
  if (s.forme === "ronde") {
    out.push({ gain: "Plus de tolérance et de maniabilité", cout: "Moins de puissance pure" });
  }
  if (s.equilibre === "haut") {
    out.push({ gain: "Plus de puissance en sortie", cout: "Plus fatigante, moins maniable" });
  }
  if (s.rigidite === "hard") {
    out.push({ gain: "Plus de précision et de réactivité", cout: "Moins de confort, plus de vibrations" });
  }
  if (s.rigidite === "soft") {
    out.push({ gain: "Plus de confort et de contrôle", cout: "Moins de puissance explosive" });
  }
  const pm =
    s.poidsMin != null && s.poidsMax != null ? (s.poidsMin + s.poidsMax) / 2 : null;
  if (pm != null && pm >= 370) {
    out.push({ gain: "Plus de stabilité sur balles lourdes", cout: "Moins de maniabilité" });
  }
  if (pm != null && pm <= 360) {
    out.push({ gain: "Plus de maniabilité et de vivacité", cout: "Moins de stabilité face à la puissance adverse" });
  }

  return out;
}
