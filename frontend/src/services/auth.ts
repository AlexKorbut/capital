import { api } from "./api";
import type { User } from "@/store/auth";

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface AuthResponse {
  user: User;
  tokens: TokenPair;
}

export async function register(body: {
  email: string;
  password: string;
  name?: string;
  base_currency?: string;
}): Promise<AuthResponse> {
  const { data } = await api.post<AuthResponse>("/auth/register", body);
  return data;
}

export async function login(body: {
  email: string;
  password: string;
  code?: string;
}): Promise<AuthResponse> {
  const { data } = await api.post<AuthResponse>("/auth/login", body);
  return data;
}

// --- Two-factor auth ----------------------------------------------------------

export interface TwoFAStatus {
  enabled: boolean;
  recovery_codes_left: number;
}

export async function get2FAStatus(): Promise<TwoFAStatus> {
  const { data } = await api.get<TwoFAStatus>("/auth/2fa/status");
  return data;
}

export async function setup2FA(): Promise<{ secret: string; otpauth_uri: string }> {
  const { data } = await api.post("/auth/2fa/setup", {});
  return data;
}

export async function enable2FA(code: string): Promise<{ recovery_codes: string[] }> {
  const { data } = await api.post("/auth/2fa/enable", { code });
  return data;
}

export async function disable2FA(code: string): Promise<Message> {
  const { data } = await api.post<Message>("/auth/2fa/disable", { code });
  return data;
}

export async function logoutAll(): Promise<TokenPair> {
  const { data } = await api.post<TokenPair>("/auth/logout-all", {});
  return data;
}

export async function fetchMe(): Promise<User> {
  const { data } = await api.get<User>("/auth/me");
  return data;
}

export interface Message {
  message: string;
}

export async function requestVerification(): Promise<Message> {
  const { data } = await api.post<Message>("/auth/verify-email/request", {});
  return data;
}

export async function confirmVerification(token: string): Promise<Message> {
  const { data } = await api.post<Message>("/auth/verify-email/confirm", { token });
  return data;
}

export async function forgotPassword(email: string): Promise<Message> {
  const { data } = await api.post<Message>("/auth/password/forgot", { email });
  return data;
}

export async function resetPassword(
  token: string,
  password: string,
): Promise<Message> {
  const { data } = await api.post<Message>("/auth/password/reset", {
    token,
    password,
  });
  return data;
}
