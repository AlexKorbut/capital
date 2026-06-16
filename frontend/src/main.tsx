import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { initObservability, initAnalytics, Sentry } from "./observability";
import { ErrorState } from "./components/states";
import { initTheme } from "./lib/theme";
import { tr } from "./lib/i18n";
import "./index.css";

initTheme();
initObservability();
initAnalytics();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 30_000 },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Sentry.ErrorBoundary
      fallback={
        <ErrorState
          error={new Error(tr(
            "Что-то пошло не так. Мы уже знаем об этом — обновите страницу.",
            "Something went wrong. We're on it — please refresh the page.",
          ))}
          onRetry={() => window.location.reload()}
        />
      }
    >
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </Sentry.ErrorBoundary>
  </React.StrictMode>,
);
