import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  CreditCard,
  Download,
  MailCheck,
  Palette,
  Sparkles,
} from "lucide-react";
import { THEMES, THEME_GROUPS } from "@/lib/theme";
import { useTheme } from "@/store/theme";
import {
  getSubscription,
  openBillingPortal,
} from "@/services/billing";
import { exportAccount, deleteAccount } from "@/services/account";
import { requestVerification } from "@/services/auth";
import { useAuth } from "@/store/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorState, LoadingState, errMessage } from "@/components/states";
import { SecuritySection } from "@/components/SecuritySection";
import { BeneficiarySection } from "@/components/BeneficiarySection";
import { CurrencySection } from "@/components/CurrencySection";
import { LanguageSection } from "@/components/LanguageSection";
import { useT } from "@/lib/i18n";

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

export function SettingsPage() {
  const t = useT();
  const navigate = useNavigate();
  const { user, clear } = useAuth();
  const { themeId, setTheme } = useTheme();
  const [confirmEmail, setConfirmEmail] = useState("");

  const sub = useQuery({
    queryKey: ["billing", "subscription"],
    queryFn: getSubscription,
  });

  const portal = useMutation({
    mutationFn: openBillingPortal,
    onSuccess: (res) => {
      window.location.href = res.url;
    },
  });

  const resend = useMutation({ mutationFn: requestVerification });

  const exporter = useMutation({
    mutationFn: exportAccount,
    onSuccess: (data) => {
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "kapital-account-export.json";
      a.click();
      URL.revokeObjectURL(url);
    },
  });

  const remove = useMutation({
    mutationFn: () => deleteAccount(confirmEmail),
    onSuccess: () => clear(),
  });

  return (
    <div className="mx-auto max-w-2xl px-4 py-10">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">{t("Настройки", "Settings")}</h1>
        <Button variant="ghost" onClick={() => navigate("/")}>
          {t("← Назад", "← Back")}
        </Button>
      </header>

      {/* Account */}
      <section className="mt-8 rounded-lg border border-border bg-card p-6">
        <h2 className="text-sm font-medium text-muted-foreground">
          {t("Аккаунт", "Account")}
        </h2>
        <p className="mt-2 font-medium">{user?.name ?? user?.email}</p>
        <p className="text-sm text-muted-foreground">{user?.email}</p>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("Базовая валюта", "Base currency")}: {user?.base_currency ?? "USD"}
        </p>
      </section>

      {/* Language */}
      <LanguageSection />

      {/* Appearance / theme switcher */}
      <section className="mt-6 rounded-lg border border-border bg-card p-6">
        <h2 className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
          <Palette className="h-4 w-4" />
          {t("Оформление", "Appearance")}
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          {t(
            "Выберите тему — она применяется мгновенно и сохраняется на этом устройстве.",
            "Pick a theme — it applies instantly and is saved on this device.",
          )}
        </p>

        {THEME_GROUPS.map((group) => (
          <div key={group} className="mt-5">
            <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {group === "Минимал и премиум"
                ? t("Минимал и премиум", "Minimal & premium")
                : group === "Бренд-стили"
                  ? t("Бренд-стили", "Brand styles")
                  : group}
            </h3>
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
              {THEMES.filter((th) => th.group === group).map((th) => {
                const active = th.id === themeId;
                return (
                  <button
                    key={th.id}
                    type="button"
                    onClick={() => setTheme(th.id)}
                    aria-pressed={active}
                    className={`flex items-center gap-3 rounded-md border p-2.5 text-left transition ${
                      active
                        ? "border-primary ring-1 ring-primary"
                        : "border-border hover:border-muted-foreground/50"
                    }`}
                  >
                    <span
                      className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-md border border-border"
                      style={{ background: th.swatches[0] }}
                    >
                      <span className="flex flex-col gap-0.5">
                        <span className="flex gap-0.5">
                          <i
                            className="block h-2 w-2 rounded-full"
                            style={{ background: th.swatches[1] }}
                          />
                          <i
                            className="block h-2 w-2 rounded-full"
                            style={{ background: th.swatches[2] }}
                          />
                        </span>
                        <i
                          className="block h-1.5 w-[18px] rounded-full"
                          style={{ background: th.swatches[3] }}
                        />
                      </span>
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5 text-sm font-medium">
                        {th.name}
                        {active && <Check className="h-3.5 w-3.5 text-primary" />}
                      </span>
                      <span className="block text-xs text-muted-foreground">
                        {th.mode === "dark" ? t("тёмная", "dark") : t("светлая", "light")}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </section>

      {/* Base/display currency */}
      <CurrencySection />

      {/* Security: 2FA + sessions */}
      <SecuritySection />

      {/* Trusted contact / inheritance */}
      <BeneficiarySection />

      {/* Subscription */}
      <section className="mt-6 rounded-lg border border-border bg-card p-6">
        <h2 className="text-sm font-medium text-muted-foreground">
          {t("Подписка", "Subscription")}
        </h2>

        {sub.isLoading && <LoadingState />}
        {sub.isError && (
          <ErrorState error={sub.error} onRetry={() => sub.refetch()} />
        )}

        {sub.data && (
          <div className="mt-2">
            <div className="flex items-center gap-2">
              <span className="text-xl font-semibold">{sub.data.label}</span>
              {sub.data.is_active_paid && (
                <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-300">
                  {t("Активна", "Active")}
                </span>
              )}
            </div>
            {sub.data.is_active_paid && sub.data.expires_at && (
              <p className="mt-1 text-sm text-muted-foreground">
                {t("Действует до", "Valid until")} {fmtDate(sub.data.expires_at)}
              </p>
            )}

            {portal.isError && (
              <p className="mt-3 text-sm text-red-500">
                {errMessage(portal.error)}
              </p>
            )}

            <div className="mt-4 flex flex-wrap gap-3">
              {sub.data.plan === "free" ? (
                <Button onClick={() => navigate("/pricing")}>
                  <Sparkles className="mr-2 h-4 w-4" />
                  {t("Перейти на Pro", "Upgrade to Pro")}
                </Button>
              ) : (
                <Button onClick={() => navigate("/pricing")} variant="outline">
                  {t("Сменить тариф", "Change plan")}
                </Button>
              )}
              {sub.data.has_stripe_customer && (
                <Button
                  variant="outline"
                  disabled={portal.isPending}
                  onClick={() => portal.mutate()}
                >
                  <CreditCard className="mr-2 h-4 w-4" />
                  {portal.isPending
                    ? t("Открываю…", "Opening…")
                    : t("Управление оплатой", "Manage billing")}
                </Button>
              )}
            </div>
          </div>
        )}
      </section>

      {/* Email verification */}
      {user && !user.email_verified && (
        <section className="mt-6 rounded-lg border border-amber-500/40 bg-amber-500/10 p-6">
          <h2 className="flex items-center gap-2 text-sm font-medium text-amber-300">
            <MailCheck className="h-4 w-4" />
            {t("Email не подтверждён", "Email not verified")}
          </h2>
          <p className="mt-2 text-sm text-muted-foreground">
            {t(
              "Подтвердите адрес, чтобы защитить аккаунт и получать уведомления.",
              "Verify your address to secure the account and receive notifications.",
            )}
          </p>
          {resend.isSuccess ? (
            <p className="mt-3 text-sm text-emerald-300">
              {t("Письмо отправлено — проверьте почту.", "Email sent — check your inbox.")}
            </p>
          ) : (
            <Button
              variant="outline"
              className="mt-4"
              disabled={resend.isPending}
              onClick={() => resend.mutate()}
            >
              {resend.isPending
                ? t("Отправляю…", "Sending…")
                : t("Отправить письмо повторно", "Resend email")}
            </Button>
          )}
        </section>
      )}

      {/* Data & privacy (GDPR) */}
      <section className="mt-6 rounded-lg border border-border bg-card p-6">
        <h2 className="text-sm font-medium text-muted-foreground">
          {t("Данные и приватность", "Data & privacy")}
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          {t(
            "Скачайте копию всех ваших данных в формате JSON.",
            "Download a copy of all your data as JSON.",
          )}
        </p>
        {exporter.isError && (
          <p className="mt-2 text-sm text-destructive">
            {errMessage(exporter.error)}
          </p>
        )}
        <Button
          variant="outline"
          className="mt-4"
          disabled={exporter.isPending}
          onClick={() => exporter.mutate()}
        >
          <Download className="mr-2 h-4 w-4" />
          {exporter.isPending
            ? t("Готовлю…", "Preparing…")
            : t("Экспортировать данные", "Export data")}
        </Button>
      </section>

      {/* Danger zone: account deletion */}
      <section className="mt-6 rounded-lg border border-red-500/40 bg-red-500/5 p-6">
        <h2 className="flex items-center gap-2 text-sm font-medium text-red-400">
          <AlertTriangle className="h-4 w-4" />
          {t("Удаление аккаунта", "Delete account")}
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          {t(
            "Это действие необратимо: будут удалены все снимки, активы и советы. Введите ваш email для подтверждения.",
            "This is irreversible: all snapshots, assets and advice will be deleted. Type your email to confirm.",
          )}
        </p>
        <Input
          type="email"
          placeholder={user?.email ?? "Email"}
          className="mt-4"
          value={confirmEmail}
          onChange={(e) => setConfirmEmail(e.target.value)}
        />
        {remove.isError && (
          <p className="mt-2 text-sm text-destructive">
            {errMessage(remove.error)}
          </p>
        )}
        <Button
          variant="outline"
          className="mt-4 border-red-500/50 text-red-400 hover:bg-red-500/10"
          disabled={
            remove.isPending ||
            confirmEmail.trim().toLowerCase() !==
              (user?.email ?? "").toLowerCase()
          }
          onClick={() => remove.mutate()}
        >
          {remove.isPending
            ? t("Удаляю…", "Deleting…")
            : t("Удалить аккаунт навсегда", "Delete account permanently")}
        </Button>
      </section>

      <section className="mt-6">
        <Button variant="outline" onClick={clear} className="w-full">
          {t("Выйти", "Sign out")}
        </Button>
      </section>
    </div>
  );
}
