import { BadgeCheck, Sparkles, HelpCircle, Store } from "lucide-react";
import { cn } from "@/lib/utils";

/* Provenance d'une donnée affichée :
   - official  : caractéristique issue de la fiche officielle fabricant
   - estimated : score estimé par notre moteur (estimation experte)
   - reseller  : donnée issue d'un revendeur sérieux
   - unknown   : donnée non communiquée                                      */
export type Provenance = "official" | "estimated" | "reseller" | "unknown";

const CONFIG: Record<
  Provenance,
  { label: string; Icon: typeof BadgeCheck; cls: string; help: string }
> = {
  official: {
    label: "Officiel",
    Icon: BadgeCheck,
    cls: "text-provenance-official border-provenance-official/40 bg-provenance-official/10",
    help: "Caractéristique issue de la fiche officielle du fabricant.",
  },
  estimated: {
    label: "Estimé",
    Icon: Sparkles,
    cls: "text-provenance-estimated border-provenance-estimated/40 bg-provenance-estimated/10",
    help: "Estimation experte calculée à partir des caractéristiques techniques — pas une donnée officielle.",
  },
  reseller: {
    label: "Revendeur",
    Icon: Store,
    cls: "text-provenance-reseller border-provenance-reseller/40 bg-provenance-reseller/10",
    help: "Donnée issue d'un revendeur sérieux (fiche officielle indisponible ou incomplète).",
  },
  unknown: {
    label: "Non communiqué",
    Icon: HelpCircle,
    cls: "text-provenance-unknown border-provenance-unknown/40 bg-provenance-unknown/10",
    help: "Cette donnée n'est pas communiquée. Nous ne l'inventons pas.",
  },
};

export function DataProvenanceBadge({
  kind,
  className,
  showLabel = true,
}: {
  kind: Provenance;
  className?: string;
  showLabel?: boolean;
}) {
  const { label, Icon, cls, help } = CONFIG[kind];
  return (
    <span
      title={help}
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide",
        cls,
        className,
      )}
    >
      <Icon className="size-3" aria-hidden />
      {showLabel && label}
    </span>
  );
}
