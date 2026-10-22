/**
 * password.ts
 * ===========
 * WHAT THIS FILE DOES
 * -------------------
 * Checks a proposed password against the SAME rules the backend enforces
 * (`password_problems` in backend/app/core/security.py), so the signup and
 * reset forms can say what is wrong as the user types instead of after a
 * round trip.
 *
 * THE RULES (and why only these -- see the backend docstring for more)
 * --------------------------------------------------------------------
 *   * at least PASSWORD_MIN_LENGTH characters (10 by default)
 *   * not made of digits only
 *   * not the account's own email address, or the part before the "@"
 *
 * Length, not composition: modern guidance (NIST SP 800-63B) is that "must
 * contain a symbol" mostly produces "Password1!", while length is what costs an
 * attacker time.
 *
 * KEEPING THE TWO IN STEP
 * -----------------------
 * The backend's minimum is the PASSWORD_MIN_LENGTH setting. A deployment that
 * raises it should set NEXT_PUBLIC_PASSWORD_MIN_LENGTH to the same value; if
 * they differ, nothing breaks -- the backend still refuses, and its 422
 * message is shown -- the hint is just less helpful.
 */

const configured = Number(process.env.NEXT_PUBLIC_PASSWORD_MIN_LENGTH);

/** Mirrors the backend's PASSWORD_MIN_LENGTH default. */
export const PASSWORD_MIN_LENGTH =
  Number.isInteger(configured) && configured > 0 ? configured : 10;

/** Everything wrong with `password`, worded as the backend words it. Empty = fine. */
export function passwordProblems(password: string, email?: string | null): string[] {
  const problems: string[] = [];
  if (password.length < PASSWORD_MIN_LENGTH) {
    problems.push(`Password must be at least ${PASSWORD_MIN_LENGTH} characters long.`);
  }
  // `/^\d+$/` matches Python's str.isdigit() for the ASCII digits that matter
  // here; an empty password is already covered by the length rule.
  if (/^\d+$/.test(password)) {
    problems.push("Password cannot be made of numbers only.");
  }
  if (email) {
    const lowered = password.trim().toLowerCase();
    const normalised = email.trim().toLowerCase();
    if (lowered === normalised || lowered === normalised.split("@")[0]) {
      problems.push("Password cannot be the same as your email address.");
    }
  }
  return problems;
}
