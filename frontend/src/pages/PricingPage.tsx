import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, Bitcoin, CreditCard } from "lucide-react";
import {
  getPlans,
  getSubscription,
  startCheckout,
  type Plan,
} from "@/services/billing";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState, errMessage } from "@/components/states";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";

function limitText(p: Plan, t: (ru: string, en: string) => string): string[] {
  const snap =
    p.max_snapshots_per_month == null
      ? t("Безлимит снимков", "Unlimited snapshots")
      : t(`${p.max_snapshots_per_month} снимка/мес`, `${p.max_snapshots_per_month} snapshots/mo`);
  const assets =
    p.max_assets_per_snapshot == null
      ? t("Безлимит активов", "Unlimited assets")
      : t(
          `до ${p.max_assets_per_snapshot} активов в снимке`,
          `up to ${p.max_assets_per_snapshot} assets per snapshot`,
        );
  const advice =
    p.advice_per_week == null
      ? t("Безлимит советов", "Unlimited advice")
      : t(`${p.advice_per_week} совет/нед`, `${p.advice_per_week} advice/wk`);
  return [
    snap,
    assets,
    advice,
    p.can_use_scenarios
      ? t("Сценарии «что если»", "What-if scenarios")
      : t("Без сценариев", "No scenarios"),
    p.can_export ? t("Экспорт данных", "Data export") : t("Без экспорта", "No export"),
  ];
}

export function PricingPage() {
  const t = useT();
  const navigate = useNavigate();
  const [pending, setPending] = useState<string | null>(null);

  const plans = useQuery({ queryKey: ["billing", "plans"], queryFn: getPlans });
  const sub = useQuery({
    queryKey: ["billing", "subscription"],
    queryFn: getSubscription,
  });

  const checkout = useMutation({
    mutationFn: (vars: {
      provider: "stripe" | "coingate";
      plan: "pro" | "business";
    }) => startCheckout(vars),
    onSuccess: (res) => {
      window.location.href = res.url;
    },
    onSettled: () => setPending(null),
  });

  if (plans.isLoading) return <LoadingState />;
  if (plans.isError)
    return (
      <div className="mx-auto max-w-3xl px-4 py-10">
        <ErrorState error={plans.error} onRetry={() => plans.refetch()} />
      </div>
    );

  const current = sub.data?.plan;

  return (
    <div className="mx-auto max-w-4xl px-4 py-10">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">{t("Тарифы", "Pricing")}</h1>
        <Button variant="ghost" onClick={() => navigate(-1)}>
          {t("← Назад", "← Back")}
        </Button>
      </header>
      <p className="mt-2 text-sm text-muted-foreground">
        {t(
          "Полный приватный контроль над капиталом. Карта — через Stripe, крипта — через CoinGate (USDT/BTC).",
          "Full private control over your wealth. Card via Stripe, crypto via CoinGate (USDT/BTC).",
        )}
      </p>

      {checkout.isError && (
        <p className="mt-4 text-sm text-red-500">{errMessage(checkout.error)}</p>
      )}

      <div className="mt-8 grid gap-6 md:grid-cols-3">
        {(plans.data ?? []).map((p) => {
          const isCurrent = current === p.name;
          const isFree = p.name === "free";
          const payable = p.name === "pro" || p.name === "business";
          return (
            <section
              key={p.name}
              className={cn(
                "flex flex-col rounded-xl border bg-card p-6",
                p.name === "pro"
                  ? "border-indigo-500/60 ring-1 ring-indigo-500/30"
                  : "border-border",
              )}
            >
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold">{p.label}</h2>
                {p.name === "pro" && (
                  <span className="rounded-full bg-indigo-500/15 px-2 py-0.5 text-xs text-indigo-300">
                    {t("Популярный", "Popular")}
                  </span>
                )}
              </div>

              <ul className="mt-4 flex-1 space-y-2 text-sm">
                {limitText(p, t).map((f) => (
                  <li key={f} className="flex items-start gap-2">
                    <Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
                    <span>{f}</span>
                  </li>
                ))}
              </ul>

              <div className="mt-6 space-y-2">
                {isCurrent ? (
                  <Button variant="outline" className="w-full" disabled>
                    {t("Текущий тариф", "Current plan")}
                  </Button>
                ) : isFree ? (
                  <Button
                    variant="outline"
                    className="w-full"
                    onClick={() => navigate("/")}
                  >
                    {t("Остаться на Free", "Stay on Free")}
                  </Button>
                ) : payable ? (
                  <>
                    <Button
                      className="w-full"
                      disabled={checkout.isPending}
                      onClick={() => {
                        setPending(`${p.name}-stripe`);
                        checkout.mutate({
                          provider: "stripe",
                          plan: p.name as "pro" | "business",
                        });
                      }}
                    >
                      <CreditCard className="mr-2 h-4 w-4" />
                      {pending === `${p.name}-stripe`
                        ? t("Открываю…", "Opening…")
                        : t("Оплатить картой", "Pay by card")}
                    </Button>
                    <Button
                      variant="outline"
                      className="w-full"
                      disabled={checkout.isPending}
                      onClick={() => {
                        setPending(`${p.name}-coingate`);
                        checkout.mutate({
                          provider: "coingate",
                          plan: p.name as "pro" | "business",
                        });
                      }}
                    >
                      <Bitcoin className="mr-2 h-4 w-4" />
                      {pending === `${p.name}-coingate`
                        ? t("Открываю…", "Opening…")
                        : t("Крипта (USDT/BTC)", "Crypto (USDT/BTC)")}
                    </Button>
                  </>
                ) : null}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
