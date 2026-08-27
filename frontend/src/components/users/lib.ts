/**
 * Pure helpers for the Users and Account UI. React-free so the rules that
 * drive disabled controls and password validation are unit-testable.
 */
import type { User } from "@/lib/api";

/** Mirrors the backend's `min_length=8` on password fields. */
export const MIN_PASSWORD_LENGTH = 8;

export function displayName(user: Pick<User, "full_name" | "email">): string {
  const name = user.full_name.trim();
  return name.length > 0 ? name : user.email;
}

/** Whether `user` is the signed-in account (controls the "You" badge / disabled actions). */
export function isSelf(
  user: Pick<User, "id">,
  current: Pick<User, "id"> | null | undefined,
): boolean {
  return current != null && current.id === user.id;
}

/** Client-side pre-check for password forms; returns a message or `null` when valid. */
export function validateNewPassword(password: string, confirm: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  if (password !== confirm) return "Passwords do not match.";
  return null;
}
