import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Coins } from "lucide-react";
import { setBaseCurrency } from "@/services/account";
import { fetchMe } from "@/services/auth";
import { useAuth } from "@/store/auth";
import { useT } from "@/lib/i18n";

const CURRENCIES = ["USD", "EUR", "BYN", "GEL", "RUB", "KZT", "PLN", "UAH", "GBP"];

export function CurrencySection() {
  const t = useT();
  const qc = useQueryClient();
  const { user, setUser } = useAuth();
  const current = user?.base_currency ?? "USD";

  const change = useMutation({
    mutationFn: (code: string) => setBaseCurrency(code),
    onSuccess: async () => {
      const me = await fetchMe();
      setUser(me);
      qc.invalidateQueries({ queryKey: ["portfolio"] });
    },
  });

  return (
    <section className="mt-6 rounded-lg border border-border bg-card p-6">
      <h2 className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
        <Coins className="h-4 w-4" />
        {t("Базовая валюта", "Base currency")}
      </h2>
      <p className="mt-2 text-sm text-muted-foreground">
        {t(
          "Итоги и графики показываются в этой валюте (курсы тянутся автоматически).",
          "Totals and charts are shown in this currency (rates fetched automatically).",
        )}
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {CURRENCIES.map((c) => (
          <button
            key={c}
            type="button"
            disabled={change.isPending}
            onClick={() => change.mutate(c)}
            className={`rounded-md border px-3 py-1.5 text-sm transition ${
              c === current
                ? "border-primary bg-primary/10 text-primary"
                : "border-border hover:border-muted-foreground/50"
            }`}
          >
            {c}
          </button>
        ))}
      </div>
    </section>
  );
}
