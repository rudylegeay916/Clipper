import type { Metadata } from "next";
import Link from "next/link";
import { Compass } from "lucide-react";
import { playerProfileSchema, type PlayerProfile } from "@/types/profile";
import { recommend } from "@/lib/scoring/engine";
import { ResultSummary } from "@/components/result/result-summary";
import { RecommendationResult } from "@/components/result/recommendation-result";
import { ButtonLink } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "Tes recommandations · PadelMatch",
};

function parseProfile(p: string | undefined): PlayerProfile | null {
  if (!p) return null;
  try {
    return playerProfileSchema.parse(JSON.parse(decodeURIComponent(p)));
  } catch {
    return null;
  }
}

export default async function ResultatsPage({
  searchParams,
}: {
  searchParams: Promise<{ p?: string }>;
}) {
  const { p } = await searchParams;
  const profile = parseProfile(p);

  if (!profile) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-24 text-center sm:px-6">
        <Compass className="mx-auto size-10 text-volt" />
        <h1 className="mt-4 font-display text-3xl font-bold">Réponds d&apos;abord au questionnaire</h1>
        <p className="mt-3 text-muted">
          Nous avons besoin de ton profil joueur pour calculer des recommandations justifiées.
        </p>
        <ButtonLink href="/questionnaire" size="lg" className="mt-6">
          Commencer le questionnaire
        </ButtonLink>
      </div>
    );
  }

  const result = recommend(profile);

  return (
    <div className="mx-auto max-w-7xl px-4 py-12 sm:px-6">
      <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-4xl font-bold">Tes recommandations</h1>
          <p className="mt-2 text-muted">Basées sur ton profil et les caractéristiques sourcées des modèles.</p>
        </div>
        <Link href="/questionnaire" className="text-sm text-volt hover:underline">
          Refaire le questionnaire
        </Link>
      </header>

      <div className="mb-10">
        <ResultSummary profile={profile} target={result.target} />
      </div>

      <RecommendationResult result={result} />
    </div>
  );
}
