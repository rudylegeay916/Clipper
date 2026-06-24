import Link from "next/link";
import { rackets, brands } from "@/data/rackets";

export function SiteFooter() {
  return (
    <footer className="mt-24 border-t border-border bg-bg-elevated">
      <div className="mx-auto max-w-7xl px-4 py-12 sm:px-6">
        <div className="grid gap-8 md:grid-cols-3">
          <div>
            <p className="font-display text-lg font-bold">
              Padel<span className="text-volt">Match</span>
            </p>
            <p className="mt-2 max-w-xs text-sm text-muted">
              Le moteur qui t&apos;aide à comprendre quelle raquette de padel correspond
              vraiment à ton jeu — sans te tromper de matériel.
            </p>
          </div>

          <nav className="text-sm">
            <p className="font-medium text-fg">Navigation</p>
            <ul className="mt-3 space-y-2 text-muted">
              <li><Link href="/questionnaire" className="hover:text-fg">Questionnaire</Link></li>
              <li><Link href="/comparateur" className="hover:text-fg">Comparateur</Link></li>
              <li><Link href="/catalogue" className="hover:text-fg">Catalogue</Link></li>
              <li><Link href="/guide" className="hover:text-fg">Guide pédagogique</Link></li>
            </ul>
          </nav>

          <div className="text-sm">
            <p className="font-medium text-fg">Méthodologie des données</p>
            <p className="mt-3 text-muted">
              <strong className="text-fg">{rackets.length} modèles réels</strong> de{" "}
              {brands.length} marques. Les caractéristiques techniques sont sourcées
              (fiche officielle ou revendeur). Les notes de comportement sont des{" "}
              <strong className="text-fg">estimations expertes</strong> calculées à partir
              de ces caractéristiques, jamais des données officielles fabricant.
            </p>
          </div>
        </div>

        <p className="mt-10 border-t border-border pt-6 text-xs text-muted-2">
          PadelMatch — outil indépendant d&apos;aide à la décision. Les marques citées
          appartiennent à leurs propriétaires respectifs. Prix indicatifs susceptibles
          d&apos;évoluer ; vérifie toujours auprès du revendeur.
        </p>
      </div>
    </footer>
  );
}
