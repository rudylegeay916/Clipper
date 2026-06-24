import type { Racket } from "@/types/racket";
import {
  FORME_LABEL,
  EQUILIBRE_LABEL,
  NOYAU_LABEL,
  RIGIDITE_LABEL,
  SWEET_SPOT_LABEL,
  materiauLabel,
} from "@/data/criteria";
import { formatPrice } from "@/lib/utils";
import { DataProvenanceBadge, type Provenance } from "@/components/shared/data-provenance-badge";
import { Card, CardBody, CardTitle } from "@/components/ui/card";

/* Provenance d'une spec objective : officielle / revendeur / non communiqué. */
function specProvenance(racket: Racket, value: unknown): Provenance {
  if (value == null || (Array.isArray(value) && value.length === 0)) return "unknown";
  return racket.isOfficialData ? "official" : "reseller";
}

function poidsLabel(racket: Racket): string | null {
  const { poidsMin, poidsMax } = racket.specs;
  if (poidsMin == null && poidsMax == null) return null;
  if (poidsMin != null && poidsMax != null) {
    return poidsMin === poidsMax ? `${poidsMin} g` : `${poidsMin}–${poidsMax} g`;
  }
  return `${poidsMin ?? poidsMax} g`;
}

export function TechnicalSpecCard({ racket }: { racket: Racket }) {
  const s = racket.specs;
  const rows: { label: string; value: string | null; raw: unknown }[] = [
    { label: "Forme", value: s.forme ? FORME_LABEL[s.forme] : null, raw: s.forme },
    { label: "Équilibre", value: s.equilibre ? EQUILIBRE_LABEL[s.equilibre] : null, raw: s.equilibre },
    { label: "Poids", value: poidsLabel(racket), raw: s.poidsMin ?? s.poidsMax },
    { label: "Noyau", value: s.noyau ? NOYAU_LABEL[s.noyau] : null, raw: s.noyau },
    { label: "Surface", value: s.surface, raw: s.surface },
    {
      label: "Matériaux",
      value: s.materiaux.length ? s.materiaux.map(materiauLabel).join(", ") : null,
      raw: s.materiaux,
    },
    {
      label: "Profil",
      value: s.profilEpaisseur != null ? `${s.profilEpaisseur} mm` : null,
      raw: s.profilEpaisseur,
    },
    { label: "Rigidité", value: s.rigidite ? RIGIDITE_LABEL[s.rigidite] : null, raw: s.rigidite },
    { label: "Sweet spot", value: s.sweetSpot ? SWEET_SPOT_LABEL[s.sweetSpot] : null, raw: s.sweetSpot },
    {
      label: "Prix indicatif",
      value: s.prixIndicatif != null ? formatPrice(s.prixIndicatif) : null,
      raw: s.prixIndicatif,
    },
  ];

  return (
    <Card>
      <CardBody>
        <div className="flex items-center justify-between">
          <CardTitle>Caractéristiques techniques</CardTitle>
          <DataProvenanceBadge kind={racket.isOfficialData ? "official" : "reseller"} />
        </div>
        <dl className="mt-4 divide-y divide-border">
          {rows.map((row) => {
            const prov = specProvenance(racket, row.raw);
            return (
              <div key={row.label} className="flex items-center justify-between gap-3 py-2.5">
                <dt className="text-sm text-muted">{row.label}</dt>
                <dd className="flex items-center gap-2 text-right">
                  {row.value ? (
                    <span className="text-sm font-medium text-fg">{row.value}</span>
                  ) : (
                    <span className="text-sm text-muted-2 italic">Non communiqué</span>
                  )}
                  {prov === "unknown" && <DataProvenanceBadge kind="unknown" showLabel={false} />}
                </dd>
              </div>
            );
          })}
        </dl>
        <p className="mt-4 text-xs text-muted-2">
          Specs vérifiées le{" "}
          {racket.lastVerifiedAt
            ? new Intl.DateTimeFormat("fr-FR", { dateStyle: "long" }).format(new Date(racket.lastVerifiedAt))
            : "—"}
          .
        </p>
      </CardBody>
    </Card>
  );
}
