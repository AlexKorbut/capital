import { useEffect, useRef } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, XCircle } from "lucide-react";
import { confirmVerification } from "@/services/auth";
import { Button } from "@/components/ui/button";
import { Spinner, errMessage } from "@/components/states";
import { useT } from "@/lib/i18n";

/** Public landing for the email-verification link: /verify-email?token=… */
export function VerifyEmailPage() {
  const t = useT();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token") ?? "";
  const fired = useRef(false);

  const mutation = useMutation({
    mutationFn: () => confirmVerification(token),
  });

  useEffect(() => {
    if (token && !fired.current) {
      fired.current = true;
      mutation.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm space-y-6 text-center">
        <h1 className="text-2xl font-bold">
          {t("Подтверждение email", "Email verification")}
        </h1>

        {!token && (
          <p className="text-sm text-destructive">
            {t("Ссылка некорректна — отсутствует токен.", "Invalid link — token is missing.")}
          </p>
        )}

        {token && mutation.isPending && (
          <div className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
            <Spinner />
            <span>{t("Проверяем ссылку…", "Verifying the link…")}</span>
          </div>
        )}

        {mutation.isSuccess && (
          <div className="space-y-2">
            <CheckCircle2 className="mx-auto h-10 w-10 text-emerald-400" />
            <p className="text-sm text-muted-foreground">
              {t("Email подтверждён. Спасибо!", "Email verified. Thank you!")}
            </p>
          </div>
        )}

        {mutation.isError && (
          <div className="space-y-2">
            <XCircle className="mx-auto h-10 w-10 text-destructive" />
            <p className="text-sm text-muted-foreground">
              {errMessage(mutation.error)}
            </p>
          </div>
        )}

        <Button className="w-full" onClick={() => navigate("/")}>
          {t("На главную", "Go home")}
        </Button>
      </div>
    </div>
  );
}
