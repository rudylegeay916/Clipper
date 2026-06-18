"use client";

import { ArrowLeft, ArrowRight, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function QuestionStep({
  step,
  total,
  title,
  subtitle,
  children,
  onBack,
  onNext,
  canNext = true,
  isLast = false,
}: {
  step: number;
  total: number;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  onBack?: () => void;
  onNext: () => void;
  canNext?: boolean;
  isLast?: boolean;
}) {
  const progress = (step / total) * 100;
  return (
    <div className="mx-auto w-full max-w-2xl">
      {/* Progression */}
      <div className="mb-8">
        <div className="flex items-center justify-between text-xs text-muted">
          <span>
            Étape {step} sur {total}
          </span>
          <span className="tabular-nums">{Math.round(progress)} %</span>
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-3">
          <div
            className="h-full rounded-full bg-volt transition-[width] duration-500"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      <div key={step} className="animate-fade-up">
        <h2 className="font-display text-2xl font-bold sm:text-3xl">{title}</h2>
        {subtitle && <p className="mt-2 text-muted">{subtitle}</p>}
        <div className="mt-6">{children}</div>
      </div>

      <div className={cn("mt-10 flex items-center gap-3", onBack ? "justify-between" : "justify-end")}>
        {onBack && (
          <Button variant="ghost" onClick={onBack}>
            <ArrowLeft className="size-4" /> Retour
          </Button>
        )}
        <Button onClick={onNext} disabled={!canNext}>
          {isLast ? (
            <>
              <Sparkles className="size-4" /> Voir mes recommandations
            </>
          ) : (
            <>
              Continuer <ArrowRight className="size-4" />
            </>
          )}
        </Button>
      </div>
    </div>
  );
}

/* ---- Contrôles réutilisables du questionnaire ---- */

export function OptionCard({
  selected,
  onClick,
  title,
  description,
}: {
  selected: boolean;
  onClick: () => void;
  title: string;
  description?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        "rounded-xl border p-4 text-left transition-all duration-200",
        selected
          ? "border-volt bg-volt/10 shadow-[0_0_0_1px_var(--volt)]"
          : "border-border bg-surface-2 hover:border-border-strong",
      )}
    >
      <span className="block font-medium text-fg">{title}</span>
      {description && <span className="mt-0.5 block text-sm text-muted">{description}</span>}
    </button>
  );
}

export function Chip({
  selected,
  onClick,
  children,
}: {
  selected: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        "rounded-full border px-4 py-2 text-sm transition-colors",
        selected
          ? "border-volt bg-volt text-volt-foreground"
          : "border-border bg-surface-2 text-fg hover:border-border-strong",
      )}
    >
      {children}
    </button>
  );
}
