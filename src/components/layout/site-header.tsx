import Link from "next/link";
import { Target } from "lucide-react";
import { ButtonLink } from "@/components/ui/button";

const NAV = [
  { href: "/questionnaire", label: "Trouver ma raquette" },
  { href: "/comparateur", label: "Comparateur" },
  { href: "/catalogue", label: "Catalogue" },
  { href: "/guide", label: "Guide" },
];

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-border bg-bg/80 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-4 px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2 font-display text-lg font-bold tracking-tight">
          <span className="grid size-8 place-items-center rounded-lg bg-volt text-volt-foreground">
            <Target className="size-5" aria-hidden />
          </span>
          Padel<span className="text-volt">Match</span>
        </Link>

        <nav className="hidden items-center gap-1 md:flex">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="rounded-lg px-3 py-2 text-sm text-muted transition-colors hover:bg-surface-2 hover:text-fg"
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <ButtonLink href="/questionnaire" size="sm" className="hidden sm:inline-flex">
          Trouver ma raquette
        </ButtonLink>
      </div>
    </header>
  );
}
