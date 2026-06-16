import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";
import { resetPassword } from "@/services/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { errMessage } from "@/components/states";
import { useT } from "@/lib/i18n";

/** Public landing for the reset link: /reset-password?token=… */
export function ResetPasswordPage() {
  const t = useT();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");

  const mutation = useMutation({
    mutationFn: () => resetPassword(token, password),
  });

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center">
          <h1 className="text-2xl font-bold">{t("Новый пароль", "New password")}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {t("Придумайте новый пароль для входа.", "Choose a new password to sign in.")}
          </p>
        </div>

        {!token ? (
          <p className="text-center text-sm text-destructive">
            {t("Ссылка некорректна — отсутствует токен.", "Invalid link — token is missing.")}
          </p>
        ) : mutation.isSuccess ? (
          <div className="space-y-4 text-center">
            <CheckCircle2 className="mx-auto h-10 w-10 text-emerald-400" />
            <p className="text-sm text-muted-foreground">
              {t(
                "Пароль обновлён. Теперь войдите с новым паролем.",
                "Password updated. Sign in with the new password.",
              )}
            </p>
            <Button className="w-full" onClick={() => navigate("/auth")}>
              {t("Войти", "Sign in")}
            </Button>
          </div>
        ) : (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              mutation.mutate();
            }}
          >
            <Input
              type="password"
              placeholder={t("Новый пароль (мин. 8 символов)", "New password (min. 8 chars)")}
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />

            {mutation.isError && (
              <p className="text-sm text-destructive">
                {errMessage(mutation.error)}
              </p>
            )}

            <Button
              type="submit"
              className="w-full"
              disabled={mutation.isPending}
            >
              {mutation.isPending
                ? t("Сохраняю…", "Saving…")
                : t("Сохранить пароль", "Save password")}
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
