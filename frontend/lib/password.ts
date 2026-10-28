// Checks a proposed password against the SAME rules the backend enforces...

const configured = Number(process.env.NEXT_PUBLIC_PASSWORD_MIN_LENGTH);

export const PASSWORD_MIN_LENGTH =
  Number.isInteger(configured) && configured > 0 ? configured : 10;

export function passwordProblems(password: string, email?: string | null): string[] {
  const problems: string[] = [];
  if (password.length < PASSWORD_MIN_LENGTH) {
    problems.push(`Password must be at least ${PASSWORD_MIN_LENGTH} characters long.`);
  }
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
