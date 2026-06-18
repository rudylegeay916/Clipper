import { Info } from "lucide-react";
import { cn } from "@/lib/utils";

/* Infobulle pédagogique (révélée au survol/focus, sans JS).
   Accessible : le contenu reste dans le DOM, lisible au clavier. */
export function EducationalTooltip({
  term,
  children,
  className,
}: {
  term: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span className={cn("group relative inline-flex items-center", className)}>
      <button
        type="button"
        className="inline-flex items-center gap-1 border-b border-dotted border-muted-2 text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-volt"
        aria-label={`Définition : ${term}`}
      >
        {term}
        <Info className="size-3 text-muted-2" aria-hidden />
      </button>
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 w-60 -translate-x-1/2 rounded-lg border border-border-strong bg-bg-elevated p-3 text-xs leading-relaxed text-muted opacity-0 shadow-xl transition-opacity duration-200 group-hover:opacity-100 group-focus-within:opacity-100"
      >
        {children}
      </span>
    </span>
  );
}
