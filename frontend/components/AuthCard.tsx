// The frame shared by every signed-out screen: sign in, forgot password, reset password...

"use client";

import { motion } from "framer-motion";
import { AlertCircle, CheckCircle2, Info } from "lucide-react";
import type { ReactNode } from "react";

import LanguageSwitcher from "@/components/app/LanguageSwitcher";
import { useT } from "@/lib/i18n";

export function AuthCard({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
}) {
  return (
    <main className="relative flex min-h-screen items-center justify-center px-6 py-12">
      <LanguageSwitcher className="absolute right-4 top-4" />
      <motion.div
        initial={{ opacity: 0, y: 24, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
        className="glass w-full max-w-md p-8"
      >
        <div className="mb-8 text-center">
          <h1 className="text-gradient text-3xl font-bold">{title}</h1>
          {subtitle && (
            <p className="mt-2 text-sm" style={{ color: "var(--text-secondary)" }}>
              {subtitle}
            </p>
          )}
        </div>
        {children}
      </motion.div>
    </main>
  );
}

type Tone = "error" | "success" | "info";

const TONES: Record<Tone, { background: string; color: string }> = {
  error: { background: "rgba(248,113,113,0.1)", color: "var(--danger)" },
  success: { background: "rgba(52,211,153,0.1)", color: "var(--success)" },
  info: { background: "rgba(99,102,241,0.1)", color: "var(--accent-hover)" },
};

export function Notice({ tone, children }: { tone: Tone; children: ReactNode }) {
  const Icon = tone === "error" ? AlertCircle : tone === "success" ? CheckCircle2 : Info;
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className="flex items-start gap-2 rounded-xl px-4 py-3 text-sm"
      style={TONES[tone]}
    >
      <Icon size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

export function PasswordHints({ id, problems, minLength }: { id: string; problems: string[]; minLength: number }) {
  const { t } = useT();
  return (
    <ul id={id} className="space-y-0.5 pl-1 text-xs" style={{ color: "var(--text-muted)" }}>
      {problems.length === 0 ? (
        <li style={{ color: "var(--success)" }}>{t("auth.passwordGood")}</li>
      ) : (
        problems.map((p) => <li key={p}>{p}</li>)
      )}
      <li>{t("auth.passwordLength", { min: minLength })}</li>
    </ul>
  );
}
