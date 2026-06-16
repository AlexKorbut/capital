import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { forgotPassword, login, register } from "@/services/auth";
import { useAuth } from "@/store/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useT } from "@/lib/i18n";

type Mode = "login" | "register" | "forgot";

export function AuthPage() {
  const t = useT();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const setSession = useAuth((s) => s.setSession);
  const initialMode: Mode =
    searchParams.get("mode") === "register" ? "register" : "login";
  const [mode, setMode] = useState<Mode>(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [needs2FA, setNeeds2FA] = useState(false);

  const mutation = useMutation({
    mutationFn: async () =>
      mode === "login"
        ? login({ email, password, code: code || undefined })
        : register({ email, password, name: name || undefined }),
    onSuccess: (res) => {
      setSession(res.user, res.tokens.access_token, res.tokens.refresh_token);
      navigate("/", { replace: true });
    },
    onError: (e) => {
      const detail = (e as AxiosError<{ detail?: string }>)?.response?.data
        ?.detail;
      if (detail === "2fa_required" || detail === "2fa_invalid") {
        setNeeds2FA(true);
      }
    },
  });

  const loginErrorText =
    mode === "login" && needs2FA
      ? t(
          "Введите 6-значный код из приложения-аутентификатора (или код восстановления).",
          "Enter the 6-digit code from your authenticator app (or a recovery code).",
        )
      : t(
          "Не удалось войти. Проверьте данные и попробуйте снова.",
          "Sign-in failed. Check your details and try again.",
        );

  const forgot = useMutation({
    mutationFn: () => forgotPassword(email),
  });

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center">
          <h1 className="text-3xl font-bold tracking-tight">КАПИТАЛЬ</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {mode === "login"
              ? t("Вход в аккаунт", "Sign in to your account")
              : mode === "register"
                ? t("Создание аккаунта", "Create an account")
                : t("Восстановление пароля", "Reset your password")}
          </p>
        </div>

        {mode === "forgot" ? (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              forgot.mutate();
            }}
          >
            <Input
              type="email"
              placeholder="Email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />

            {forgot.isSuccess ? (
              <p className="text-sm text-muted-foreground">
                {t(
                  "Если такой email зарегистрирован, мы отправили ссылку для сброса пароля. Проверьте почту.",
                  "If that email is registered, we've sent a password reset link. Check your inbox.",
                )}
              </p>
            ) : (
              <Button
                type="submit"
                className="w-full"
                disabled={forgot.isPending}
              >
                {forgot.isPending ? "..." : t("Отправить ссылку", "Send link")}
              </Button>
            )}
          </form>
        ) : (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              mutation.mutate();
            }}
          >
            {mode === "register" && (
              <Input
                placeholder={t("Имя (необязательно)", "Name (optional)")}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            )}
            <Input
              type="email"
              placeholder="Email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <Input
              type="password"
              placeholder={t("Пароль (мин. 8 символов)", "Password (min. 8 chars)")}
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />

            {mode === "login" && needs2FA && (
              <Input
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder={t("Код 2FA (или код восстановления)", "2FA code (or recovery code)")}
                value={code}
                onChange={(e) => setCode(e.target.value)}
              />
            )}

            {(mutation.isError || (mode === "login" && needs2FA)) && (
              <p
                className={
                  needs2FA
                    ? "text-sm text-muted-foreground"
                    : "text-sm text-destructive"
                }
              >
                {loginErrorText}
              </p>
            )}

            <Button
              type="submit"
              className="w-full"
              disabled={mutation.isPending}
            >
              {mutation.isPending
                ? "..."
                : mode === "login"
                  ? t("Войти", "Sign in")
                  : t("Зарегистрироваться", "Sign up")}
            </Button>

            {mode === "login" && (
              <button
                type="button"
                className="block w-full text-center text-sm text-muted-foreground hover:underline"
                onClick={() => setMode("forgot")}
              >
                {t("Забыли пароль?", "Forgot password?")}
              </button>
            )}
          </form>
        )}

        <p className="text-center text-sm text-muted-foreground">
          {mode === "login"
            ? t("Нет аккаунта? ", "No account? ")
            : t("Уже есть аккаунт? ", "Already have an account? ")}
          <button
            className="text-primary hover:underline"
            onClick={() => setMode(mode === "login" ? "register" : "login")}
          >
            {mode === "login" ? t("Создать", "Create one") : t("Войти", "Sign in")}
          </button>
        </p>

        <p className="text-center text-xs text-muted-foreground">
          <Link to="/legal/tos" className="hover:underline">
            {t("Условия", "Terms")}
          </Link>
          {" · "}
          <Link to="/legal/privacy" className="hover:underline">
            {t("Конфиденциальность", "Privacy")}
          </Link>
          {" · "}
          <Link to="/legal/disclaimer" className="hover:underline">
            {t("Дисклеймер", "Disclaimer")}
          </Link>
        </p>
      </div>
    </div>
  );
}
