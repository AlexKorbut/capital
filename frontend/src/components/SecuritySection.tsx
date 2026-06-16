import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import {
  disable2FA,
  enable2FA,
  get2FAStatus,
  logoutAll,
  setup2FA,
} from "@/services/auth";
import { getNotifications, setNotifications } from "@/services/account";
import { useAuth } from "@/store/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { errMessage } from "@/components/states";
import { useT } from "@/lib/i18n";

export function SecuritySection() {
  const t = useT();
  const setTokens = useAuth((s) => s.setTokens);
  const status = useQuery({ queryKey: ["2fa", "status"], queryFn: get2FAStatus });

  const [setupData, setSetupData] = useState<{
    secret: string;
    otpauth_uri: string;
  } | null>(null);
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState<string[] | null>(null);
  const [disableCode, setDisableCode] = useState("");

  const setup = useMutation({
    mutationFn: setup2FA,
    onSuccess: (d) => setSetupData(d),
  });
  const enable = useMutation({
    mutationFn: () => enable2FA(code.trim()),
    onSuccess: (d) => {
      setRecovery(d.recovery_codes);
      setSetupData(null);
      setCode("");
      status.refetch();
    },
  });
  const disable = useMutation({
    mutationFn: () => disable2FA(disableCode.trim()),
    onSuccess: () => {
      setDisableCode("");
      setRecovery(null);
      status.refetch();
    },
  });
  const logoutEverywhere = useMutation({
    mutationFn: logoutAll,
    onSuccess: (tokens) => {
      // Keep THIS session alive with the freshly-issued pair.
      setTokens(tokens.access_token, tokens.refresh_token);
    },
  });

  const notif = useQuery({ queryKey: ["notifications"], queryFn: getNotifications });
  const toggleNotif = useMutation({
    mutationFn: (v: boolean) => setNotifications(v),
    onSuccess: () => notif.refetch(),
  });

  const enabled = status.data?.enabled;

  return (
    <section className="mt-6 rounded-lg border border-border bg-card p-6">
      <h2 className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
        <ShieldCheck className="h-4 w-4" />
        {t("Безопасность", "Security")}
      </h2>

      {/* 2FA */}
      <div className="mt-4">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">
              {t("Двухфакторная аутентификация", "Two-factor authentication")}
            </p>
            <p className="text-xs text-muted-foreground">
              {enabled
                ? t(
                    `Включена · кодов восстановления: ${status.data?.recovery_codes_left ?? 0}`,
                    `Enabled · recovery codes left: ${status.data?.recovery_codes_left ?? 0}`,
                  )
                : t(
                    "Защитите вход кодом из приложения-аутентификатора.",
                    "Protect sign-in with a code from an authenticator app.",
                  )}
            </p>
          </div>
          {!enabled && !setupData && (
            <Button
              variant="outline"
              disabled={setup.isPending}
              onClick={() => setup.mutate()}
            >
              {setup.isPending ? "…" : t("Включить", "Enable")}
            </Button>
          )}
        </div>

        {/* Enrolment flow */}
        {setupData && (
          <div className="mt-4 rounded-md border border-border p-4">
            <p className="text-sm">
              {t(
                "1. Добавьте ключ в Google Authenticator / Authy и введите 6-значный код.",
                "1. Add the key to Google Authenticator / Authy and enter the 6-digit code.",
              )}
            </p>
            <p className="mt-2 break-all rounded bg-muted px-2 py-1 font-mono text-xs">
              {setupData.secret}
            </p>
            <p className="mt-1 break-all text-[11px] text-muted-foreground">
              {setupData.otpauth_uri}
            </p>
            <div className="mt-3 flex gap-2">
              <Input
                inputMode="numeric"
                placeholder="123456"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                className="max-w-[140px]"
              />
              <Button
                  disabled={enable.isPending || code.trim().length < 6}
                onClick={() => enable.mutate()}
              >
                {enable.isPending ? "…" : t("Подтвердить", "Confirm")}
              </Button>
              <Button
                  variant="ghost"
                onClick={() => {
                  setSetupData(null);
                  setCode("");
                }}
              >
                {t("Отмена", "Cancel")}
              </Button>
            </div>
            {enable.isError && (
              <p className="mt-2 text-sm text-destructive">
                {errMessage(enable.error)}
              </p>
            )}
          </div>
        )}

        {/* Recovery codes (shown once) */}
        {recovery && (
          <div className="mt-4 rounded-md border border-amber-500/40 bg-amber-500/10 p-4">
            <p className="text-sm font-medium text-amber-700 dark:text-amber-300">
              {t(
                "Сохраните коды восстановления — они показываются один раз.",
                "Save your recovery codes — they're shown only once.",
              )}
            </p>
            <div className="mt-2 grid grid-cols-2 gap-1 font-mono text-sm">
              {recovery.map((c) => (
                <span key={c}>{c}</span>
              ))}
            </div>
            <Button
              variant="outline"
              className="mt-3"
              onClick={() => setRecovery(null)}
            >
              {t("Я сохранил коды", "I've saved the codes")}
            </Button>
          </div>
        )}

        {/* Disable */}
        {enabled && !recovery && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Input
              inputMode="numeric"
              placeholder={t("Код для отключения", "Code to disable")}
              value={disableCode}
              onChange={(e) => setDisableCode(e.target.value)}
              className="max-w-[200px]"
            />
            <Button
              variant="outline"
              className="border-red-500/50 text-red-500 hover:bg-red-500/10"
              disabled={disable.isPending || disableCode.trim().length < 4}
              onClick={() => disable.mutate()}
            >
              {disable.isPending ? "…" : t("Отключить 2FA", "Disable 2FA")}
            </Button>
            {disable.isError && (
              <p className="w-full text-sm text-destructive">
                {errMessage(disable.error)}
              </p>
            )}
          </div>
        )}
      </div>

      {/* Sessions */}
      <div className="mt-6 border-t border-border pt-4">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">
              {t("Сессии на устройствах", "Device sessions")}
            </p>
            <p className="text-xs text-muted-foreground">
              {t(
                "Завершить вход на всех других устройствах (текущее останется активным).",
                "Sign out of all other devices (this one stays active).",
              )}
            </p>
          </div>
          <Button
            variant="outline"
            disabled={logoutEverywhere.isPending}
            onClick={() => logoutEverywhere.mutate()}
          >
            {logoutEverywhere.isPending ? "…" : t("Выйти везде", "Sign out everywhere")}
          </Button>
        </div>
        {logoutEverywhere.isSuccess && (
          <p className="mt-2 text-sm text-emerald-500">
            {t("Все другие сессии завершены.", "All other sessions ended.")}
          </p>
        )}
      </div>

      {/* Email notifications */}
      <div className="mt-6 flex items-center justify-between border-t border-border pt-4">
        <div>
          <p className="text-sm font-medium">
            {t("Письма-уведомления", "Email notifications")}
          </p>
          <p className="text-xs text-muted-foreground">
            {t(
              "Напоминания обновить портфель и еженедельный дайджест.",
              "Portfolio update reminders and the weekly digest.",
            )}
          </p>
        </div>
        <Button
          variant="outline"
          disabled={toggleNotif.isPending || notif.data == null}
          onClick={() => toggleNotif.mutate(!notif.data?.emails_enabled)}
        >
          {notif.data?.emails_enabled ? t("Включены", "On") : t("Выключены", "Off")}
        </Button>
      </div>
    </section>
  );
}
