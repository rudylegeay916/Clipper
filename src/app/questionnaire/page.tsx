import type { Metadata } from "next";
import { brands } from "@/data/rackets";
import { PlayerProfileForm } from "@/components/questionnaire/player-profile-form";

export const metadata: Metadata = {
  title: "Questionnaire · PadelMatch",
  description:
    "6 étapes pour révéler ton profil joueur et obtenir une recommandation de raquette justifiée.",
};

export default function QuestionnairePage() {
  return (
    <div className="mx-auto max-w-7xl px-4 py-12 sm:px-6">
      <PlayerProfileForm brands={brands} />
    </div>
  );
}
