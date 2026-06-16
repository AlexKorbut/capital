import { api } from "@/services/api";

export interface Plan {
  name: string;
  label: string;
  max_snapshots_per_month: number | null;
  max_assets_per_snapshot: number | null;
  advice_per_week: number | null;
  can_use_scenarios: boolean;
  can_export: boolean;
}

export interface Subscription {
  plan: string;
  label: string;
  expires_at: string | null;
  is_active_paid: boolean;
  has_stripe_customer: boolean;
}

export interface CheckoutResponse {
  url: string;
  provider: string;
  reference: string | null;
}

export async function getPlans(): Promise<Plan[]> {
  const { data } = await api.get<Plan[]>("/billing/plans");
  return data;
}

export async function getSubscription(): Promise<Subscription> {
  const { data } = await api.get<Subscription>("/billing/subscription");
  return data;
}

export async function startCheckout(params: {
  provider: "stripe" | "coingate";
  plan: "pro" | "business";
  interval?: "monthly" | "yearly";
}): Promise<CheckoutResponse> {
  const { data } = await api.post<CheckoutResponse>("/billing/checkout", {
    interval: "monthly",
    ...params,
  });
  return data;
}

export async function openBillingPortal(): Promise<CheckoutResponse> {
  const { data } = await api.post<CheckoutResponse>("/billing/portal", {});
  return data;
}
