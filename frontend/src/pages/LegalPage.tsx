import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getLegal } from "@/services/legal";
import { Markdown } from "@/components/Markdown";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState } from "@/components/states";
import { useT, useLang } from "@/lib/i18n";

/** Public document page: /legal/tos | /legal/privacy | /legal/disclaimer. */
export function LegalPage() {
  const t = useT();
  const lang = useLang((s) => s.lang);
  const { slug = "" } = useParams();
  const navigate = useNavigate();

  const doc = useQuery({
    queryKey: ["legal", slug, lang],
    queryFn: () => getLegal(slug, lang),
    enabled: Boolean(slug),
  });

  return (
    <div className="mx-auto max-w-2xl px-4 py-10">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">
          {doc.data?.title ?? t("Документ", "Document")}
        </h1>
        <Button variant="ghost" onClick={() => navigate(-1)}>
          {t("← Назад", "← Back")}
        </Button>
      </header>

      {doc.isLoading && <LoadingState />}
      {doc.isError && (
        <ErrorState error={doc.error} onRetry={() => doc.refetch()} />
      )}
      {doc.data && (
        <article className="mt-6">
          <Markdown source={doc.data.markdown} />
        </article>
      )}
    </div>
  );
}
