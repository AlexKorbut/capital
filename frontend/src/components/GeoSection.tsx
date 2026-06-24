import { Globe } from "lucide-react";
import type { BreakdownEntry } from "@/services/portfolio";
import { useT } from "@/lib/i18n";
import { formatUsd as usd } from "@/lib/utils";

const FLAGS: Record<string, string> = {
  BY: "🇧🇾", GE: "🇬🇪", RU: "🇷🇺", PL: "🇵🇱", DE: "🇩🇪", US: "🇺🇸",
  KZ: "🇰🇿", UA: "🇺🇦", GB: "🇬🇧", AE: "🇦🇪", TR: "🇹🇷", AM: "🇦🇲",
};
const COUNTRY_EN: Record<string, string> = {
  BY: "Belarus", GE: "Georgia", RU: "Russia", PL: "Poland", DE: "Germany",
  US: "USA", KZ: "Kazakhstan", UA: "Ukraine", GB: "UK", AE: "UAE",
  TR: "Turkey", AM: "Armenia",
};
const COUNTRY_RU: Record<string, string> = {
  BY: "Беларусь", GE: "Грузия", RU: "Россия", PL: "Польша", DE: "Германия",
  US: "США", KZ: "Казахстан", UA: "Украина", GB: "Великобритания", AE: "ОАЭ",
  TR: "Турция", AM: "Армения",
};


export function GeoSection({ byCountry }: { byCountry: BreakdownEntry[] }) {
  const t = useT();
  const countryLabel = (code: string): string => {
    if (code === "—" || !code) return t("Без страны", "No country");
    const name = t(COUNTRY_RU[code] ?? code, COUNTRY_EN[code] ?? code);
    return FLAGS[code] ? `${name} ${FLAGS[code]}` : name;
  };
  const entries = byCountry.filter((e) => e.usd_value != null);
  const total = entries.reduce((s, e) => s + Number(e.usd_value), 0);
  if (entries.length === 0) return null;

  const single = entries.length === 1;

  return (
    <section className="rounded-lg border border-border bg-card p-6">
      <h2 className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
        <Globe className="h-4 w-4" />
        {t("По странам / юрисдикциям", "By country / jurisdiction")}
      </h2>

      <div className="mt-4 space-y-3">
        {entries.map((e) => {
          const pct = total > 0 ? (Number(e.usd_value) / total) * 100 : 0;
          return (
            <div key={e.key} className="text-sm">
              <div className="flex items-center justify-between">
                <span>{countryLabel(e.key)}</span>
                <span className="tabular-nums text-muted-foreground">
                  {usd(e.usd_value)} · {pct.toFixed(1)}%
                </span>
              </div>
              <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary"
                  style={{ width: `${Math.min(pct, 100)}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>

      <p className="mt-3 text-xs text-muted-foreground">
        {single
          ? t(
              "Весь капитал — в одной юрисдикции. Географической диверсификации нет.",
              "All capital is in one jurisdiction. No geographic diversification.",
            )
          : t(
              `Капитал распределён между ${entries.length} странами. Высокая страновая концентрация связывает капитал с локальными рисками.`,
              `Capital is spread across ${entries.length} countries. High country concentration ties your wealth to local risks.`,
            )}
      </p>
    </section>
  );
}
