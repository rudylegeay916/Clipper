import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Formate un prix indicatif (€) ou « non communiqué » si null. */
export function formatPrice(value: number | null, currency = "EUR"): string {
  if (value == null) return "Non communiqué";
  return new Intl.NumberFormat("fr-FR", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(value);
}

/** Affiche une valeur sourcée ou « Non communiqué » si null/undefined. */
export function orUnknown(value: string | number | null | undefined, suffix = ""): string {
  if (value == null || value === "") return "Non communiqué";
  return `${value}${suffix}`;
}
