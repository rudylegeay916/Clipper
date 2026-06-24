"use client";

import { useState } from "react";
import type { Racket } from "@/types/racket";
import { cn } from "@/lib/utils";

/* Visuel d'une raquette :
   - si image sourcée disponible → <img> (avec fallback si chargement échoue)
   - sinon → fallback branded premium (dégradé + glyphe + marque/modèle)
   Aucune image inventée : seules les URLs sourcées sont utilisées. */

function brandHue(brand: string): number {
  let h = 0;
  for (let i = 0; i < brand.length; i++) h = (h * 31 + brand.charCodeAt(i)) % 360;
  return h;
}

function Fallback({ racket }: { racket: Racket }) {
  const hue = brandHue(racket.marque);
  return (
    <div
      className="relative flex size-full flex-col items-center justify-center overflow-hidden"
      style={{
        background: `radial-gradient(120% 120% at 30% 20%, hsl(${hue} 45% 18%), var(--surface) 70%)`,
      }}
      aria-hidden
    >
      {/* glyphe raquette stylisé */}
      <svg viewBox="0 0 100 140" className="h-2/3 opacity-70" fill="none">
        <ellipse cx="50" cy="48" rx="34" ry="42" stroke="var(--volt)" strokeWidth="2.5" />
        <path d="M50 90 L50 122 M40 130 L60 130" stroke="var(--volt)" strokeWidth="3" strokeLinecap="round" />
        <g stroke="rgba(255,255,255,0.18)" strokeWidth="1">
          <line x1="30" y1="20" x2="30" y2="78" />
          <line x1="42" y1="12" x2="42" y2="86" />
          <line x1="58" y1="12" x2="58" y2="86" />
          <line x1="70" y1="20" x2="70" y2="78" />
          <line x1="20" y1="48" x2="80" y2="48" />
          <line x1="22" y1="32" x2="78" y2="32" />
          <line x1="22" y1="64" x2="78" y2="64" />
        </g>
      </svg>
      <div className="absolute bottom-3 left-3 right-3 text-center">
        <p className="text-[10px] uppercase tracking-widest text-muted-2">{racket.marque}</p>
        <p className="truncate font-display text-sm font-semibold text-fg/80">{racket.modele}</p>
      </div>
    </div>
  );
}

export function RacketImage({
  racket,
  className,
  rounded = "rounded-xl",
  aspectClass = "aspect-[4/3]",
}: {
  racket: Racket;
  className?: string;
  rounded?: string;
  aspectClass?: string;
}) {
  const [errored, setErrored] = useState(false);
  const showImage = racket.image && !errored;

  return (
    <div className={cn("relative overflow-hidden bg-surface-2", aspectClass, rounded, className)}>
      {showImage ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={racket.image!.url}
          alt={racket.image!.alt}
          loading="lazy"
          onError={() => setErrored(true)}
          className="size-full object-contain"
        />
      ) : (
        <Fallback racket={racket} />
      )}
    </div>
  );
}
