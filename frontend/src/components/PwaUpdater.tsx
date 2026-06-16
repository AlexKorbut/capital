import { useRegisterSW } from "virtual:pwa-register/react";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";

/**
 * Tiny toast that appears when a new service-worker build is available or when
 * the app is ready to work offline. Registration uses `autoUpdate`, so we only
 * surface a gentle "reload to update" nudge.
 */
export function PwaUpdater() {
  const t = useT();
  const {
    offlineReady: [offlineReady, setOfflineReady],
    needRefresh: [needRefresh, setNeedRefresh],
    updateServiceWorker,
  } = useRegisterSW();

  if (!offlineReady && !needRefresh) return null;

  const close = () => {
    setOfflineReady(false);
    setNeedRefresh(false);
  };

  return (
    <div className="fixed inset-x-0 bottom-20 z-50 mx-auto w-fit max-w-[90%] rounded-lg border border-border bg-card px-4 py-3 shadow-lg md:bottom-6">
      <div className="flex items-center gap-3 text-sm">
        <span>
          {needRefresh
            ? t("Доступно обновление приложения.", "An app update is available.")
            : t("Приложение готово к работе офлайн.", "The app is ready to work offline.")}
        </span>
        {needRefresh ? (
          <Button
            className="h-8 px-3 text-xs"
            onClick={() => updateServiceWorker(true)}
          >
            {t("Обновить", "Update")}
          </Button>
        ) : (
          <Button
            variant="outline"
            className="h-8 px-3 text-xs"
            onClick={close}
          >
            {t("Ок", "OK")}
          </Button>
        )}
      </div>
    </div>
  );
}
