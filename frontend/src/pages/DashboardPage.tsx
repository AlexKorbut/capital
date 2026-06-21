import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  Area,
  AreaChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchMe } from "@/services/auth";
import {
  getCurrentPortfolio,
  getHistoryChart,
  getReturns,
  useAssetLabels,
  type BreakdownEntry,
} from "@/services/portfolio";
import { useT } from "@/lib/i18n";
import { useAuth } from "@/store/auth";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { GoalsSection } from "@/components/GoalsSection";
import { WalletsSection } from "@/components/WalletsSection";
import { AllocationSection } from "@/components/AllocationSection";
import { GeoSection } from "@/components/GeoSection";
import { AssetsEditor } from "@/components/AssetsEditor";

const PIE_COLORS = [
  "#6366f1",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#3b82f6",
  "#8b5cf6",
  "#14b8a6",
];

function signedUsd(value: string | null | undefined): string {
  if (value == null) return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return (
    sign +
    n.toLocaleString("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
  );
}

function changeClass(value: string | null | undefined): string {
  const n = Number(value ?? 0);
  if (n > 0) return "text-emerald-500";
  if (n < 0) return "text-red-500";
  return "text-muted-foreground";
}

// Format a USD amount in the user's base currency (USD stays canonical on the wire).
// When usdPerBase is not a usable rate (not finite or <= 0), fall back to showing
// the raw USD value labelled as USD rather than silently treating the rate as 1.
function fmtBase(
  usdValue: string | number | null | undefined,
  baseCcy: string,
  usdPerBase: number,
): string {
  if (usdValue == null) return "—";
  const n = Number(usdValue);
  if (Number.isNaN(n)) return "—";
  const rateUsable = Number.isFinite(usdPerBase) && usdPerBase > 0;
  const inBase = rateUsable ? n / usdPerBase : n;
  const ccy = rateUsable ? baseCcy || "USD" : "USD";
  try {
    return inBase.toLocaleString("ru-RU", {
      style: "currency",
      currency: ccy,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  } catch {
    return `${inBase.toLocaleString("ru-RU", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })} ${ccy}`;
  }
}

export function DashboardPage() {
  const t = useT();
  const labels = useAssetLabels();
  const navigate = useNavigate();
  const { user, setUser, clear } = useAuth();
  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: fetchMe,
    initialData: user ?? undefined,
  });
  // Keep the persisted auth store fresh (email_verified, plan, base currency)
  // when the server returns updated data. Guarded so it can't loop.
  useEffect(() => {
    const me = meQuery.data;
    if (me && JSON.stringify(me) !== JSON.stringify(user)) {
      setUser(me);
    }
  }, [meQuery.data, user, setUser]);

  const current = useQuery({
    queryKey: ["portfolio", "current"],
    queryFn: getCurrentPortfolio,
  });
  const history = useQuery({
    queryKey: ["portfolio", "history"],
    queryFn: getHistoryChart,
  });
  const returns = useQuery({
    queryKey: ["portfolio", "returns"],
    queryFn: getReturns,
  });

  const portfolio = current.data;
  const baseCcy = portfolio?.base_currency ?? "USD";
  const usdPerBase = Number(portfolio?.usd_per_base ?? 1) || 1;
  const money = (v: string | number | null | undefined) =>
    fmtBase(v, baseCcy, usdPerBase);
  const byType = (portfolio?.breakdown.by_type ?? []).map((e: BreakdownEntry) => ({
    name: labels[e.key as keyof typeof labels] ?? e.key,
    value: Number(e.usd_value ?? 0),
  }));
  const chartData = (history.data ?? [])
    .filter((p) => p.total_usd != null)
    .map((p) => ({
      date: p.date ? new Date(p.date).toLocaleDateString("ru-RU") : "",
      total: Number(p.total_usd),
    }));

  return (
    <div className="mx-auto max-w-4xl px-4 py-10">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">КАПИТАЛЬ</h1>
        <Button variant="outline" onClick={clear}>
          {t("Выйти", "Sign out")}
        </Button>
      </header>

      {current.isLoading && <LoadingState />}

      {current.isError && (
        <ErrorState error={current.error} onRetry={() => current.refetch()} />
      )}

      {!current.isLoading && !current.isError && !portfolio && (
        <EmptyState
          title={t("Пока нет данных", "No data yet")}
          hint={t(
            "Добавьте первые активы — и здесь появится ваш капитал.",
            "Add your first assets — your net worth will show up here.",
          )}
          action={{
            label: t("Добавить активы", "Add assets"),
            onClick: () => navigate("/input"),
          }}
        />
      )}

      {portfolio && (
        <div className="mt-8 space-y-6">
          {/* Net worth */}
          <section className="rounded-lg border border-border bg-card p-6">
            <p className="text-sm text-muted-foreground">
              {t("Чистый капитал", "Net worth")}
              {baseCcy !== "USD" ? ` · ${baseCcy}` : ""}
            </p>
            <p className="mt-1 text-4xl font-bold tracking-tight">
              {money(portfolio.total_usd)}
            </p>
            {portfolio.estimated_total_usd &&
              portfolio.estimated_total_usd !== portfolio.total_usd && (
                <p className="mt-1 text-sm text-muted-foreground">
                  {t(
                    "Оценка сегодня (с учётом удорожания активов):",
                    "Estimated today (incl. asset appreciation):",
                  )}{" "}
                  <span className="font-medium text-foreground">
                    {money(portfolio.estimated_total_usd)}
                  </span>
                </p>
              )}
            {portfolio.created_at && (
              <p className="mt-1 text-xs text-muted-foreground">
                {t("снимок от", "snapshot from")}{" "}
                {new Date(portfolio.created_at).toLocaleString()}
              </p>
            )}
          </section>

          {/* Returns / net-worth growth */}
          {returns.data && returns.data.windows.length > 0 && (
            <section className="rounded-lg border border-border bg-card p-6">
              <div className="flex items-baseline justify-between">
                <h2 className="text-sm font-medium text-muted-foreground">
                  {t("Рост капитала", "Net-worth growth")}
                </h2>
                {returns.data.cagr_pct != null && (
                  <span className="text-xs text-muted-foreground">
                    CAGR{" "}
                    <span className={changeClass(returns.data.cagr_pct)}>
                      {Number(returns.data.cagr_pct) > 0 ? "+" : ""}
                      {returns.data.cagr_pct}%
                    </span>{" "}
                    · {returns.data.snapshots_count} {t("снимков", "snapshots")}
                  </span>
                )}
              </div>
              <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
                {returns.data.windows.map((w) => (
                  <div
                    key={w.key}
                    className="rounded-md border border-border px-3 py-2.5"
                  >
                    <p className="text-xs text-muted-foreground">
                      {(
                        {
                          "7d": t("7 дней", "7 days"),
                          "30d": t("30 дней", "30 days"),
                          "90d": t("90 дней", "90 days"),
                          "1y": t("1 год", "1 year"),
                          all: t("Всё время", "All time"),
                        } as Record<string, string>
                      )[w.key] ?? w.label}
                      {w.partial ? " *" : ""}
                    </p>
                    <p
                      className={`mt-1 text-sm font-semibold tabular-nums ${changeClass(
                        w.change_usd,
                      )}`}
                    >
                      {signedUsd(w.change_usd)}
                    </p>
                    {w.change_pct != null && (
                      <p className={`text-xs tabular-nums ${changeClass(w.change_pct)}`}>
                        {Number(w.change_pct) > 0 ? "+" : ""}
                        {w.change_pct}%
                      </p>
                    )}
                  </div>
                ))}
              </div>
              {returns.data.windows.some((w) => w.partial) && (
                <p className="mt-3 text-xs text-muted-foreground">
                  {t(
                    "* за весь доступный период (снимков пока меньше окна).",
                    "* over all available history (fewer snapshots than the window).",
                  )}
                </p>
              )}
              <p className="mt-2 text-xs text-muted-foreground">
                {t(
                  "Изменение чистого капитала между снимками. Это не инвестиционная доходность — пополнения и изъятия не разделяются.",
                  "Net-worth change between snapshots. This is not investment return — contributions and withdrawals aren't separated.",
                )}
              </p>
            </section>
          )}

          {/* Goals */}
          <GoalsSection />

          {/* Target allocation & drift */}
          <AllocationSection />

          {/* Geo / jurisdiction breakdown */}
          <GeoSection byCountry={portfolio.breakdown.by_country} />

          {/* Breakdown + history */}
          <div className="grid gap-6 md:grid-cols-2">
            <section className="rounded-lg border border-border bg-card p-6">
              <h2 className="text-sm font-medium text-muted-foreground">
                {t("Структура по типам", "Breakdown by type")}
              </h2>
              {byType.length > 0 ? (
                <div className="mt-2 h-56">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={byType}
                        dataKey="value"
                        nameKey="name"
                        innerRadius={50}
                        outerRadius={80}
                        paddingAngle={2}
                      >
                        {byType.map((_, i) => (
                          <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                        ))}
                      </Pie>
                      <Tooltip formatter={(v: number) => money(String(v))} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <p className="mt-4 text-sm text-muted-foreground">
                  {t("Нет оценённых активов.", "No valued assets yet.")}
                </p>
              )}
              <ul className="mt-2 space-y-1 text-sm">
                {byType.map((e, i) => (
                  <li key={i} className="flex items-center justify-between">
                    <span className="flex items-center gap-2">
                      <span
                        className="inline-block h-2 w-2 rounded-full"
                        style={{ background: PIE_COLORS[i % PIE_COLORS.length] }}
                      />
                      {e.name}
                    </span>
                    <span className="text-muted-foreground">
                      {money(String(e.value))}
                    </span>
                  </li>
                ))}
              </ul>
            </section>

            <section className="rounded-lg border border-border bg-card p-6">
              <h2 className="text-sm font-medium text-muted-foreground">
                {t("Динамика капитала", "Net-worth over time")}
              </h2>
              {chartData.length > 1 ? (
                <div className="mt-2 h-56">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={chartData}>
                      <defs>
                        <linearGradient id="nw" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#6366f1" stopOpacity={0.4} />
                          <stop offset="100%" stopColor="#6366f1" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <XAxis dataKey="date" fontSize={11} tickLine={false} />
                      <YAxis
                        fontSize={11}
                        tickLine={false}
                        width={48}
                        tickFormatter={(v: number) =>
                          `${Math.round(v / 1000)}k`
                        }
                      />
                      <Tooltip formatter={(v: number) => money(String(v))} />
                      <Area
                        type="monotone"
                        dataKey="total"
                        stroke="#6366f1"
                        fill="url(#nw)"
                        strokeWidth={2}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <p className="mt-4 text-sm text-muted-foreground">
                  {t(
                    "Добавьте ещё снимки, чтобы увидеть динамику.",
                    "Add more snapshots to see the trend.",
                  )}
                </p>
              )}
            </section>
          </div>

          {/* Asset list (editable) */}
          <AssetsEditor assets={portfolio.assets} money={money} />
        </div>
      )}

      {/* Wallets — always available (can bootstrap a portfolio from balances) */}
      {!current.isLoading && !current.isError && (
        <div className="mt-6">
          <WalletsSection />
        </div>
      )}
    </div>
  );
}
