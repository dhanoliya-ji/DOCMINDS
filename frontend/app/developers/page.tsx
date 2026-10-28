// Page /developers -- public API documentation for people integrating with DocMinds from their own code: how...

"use client";

import { ArrowLeft, Check, Copy, ExternalLink } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import LanguageSwitcher from "@/components/app/LanguageSwitcher";
import { API_BASE } from "@/lib/api";
import { rich, useT } from "@/lib/i18n";

const LIMITS = [
  { endpoint: "POST /api/v1/chat/sessions/{id}/messages", limit: "30 / minute", per: "user" },
  { endpoint: "POST /api/v1/search/", limit: "60 / minute", per: "user" },
  { endpoint: "POST /api/v1/documents/upload", limit: "30 / minute", per: "user" },
  { endpoint: "POST /api/v1/auth/login", limit: "20 / minute", per: "ip" },
] as const;

const ENDPOINTS = [
  { method: "GET", path: "/api/v1/users/me", key: "dev.ep.me" },
  { method: "GET · POST", path: "/api/v1/projects/", key: "dev.ep.projects" },
  { method: "GET", path: "/api/v1/documents/?project_id=", key: "dev.ep.documents" },
  { method: "POST", path: "/api/v1/documents/upload", key: "dev.ep.upload" },
  { method: "POST", path: "/api/v1/search/", key: "dev.ep.search" },
  { method: "POST", path: "/api/v1/chat/sessions", key: "dev.ep.sessions" },
  { method: "POST", path: "/api/v1/chat/sessions/{id}/messages", key: "dev.ep.ask" },
  { method: "POST", path: "/api/v1/chat/sessions/{id}/messages/stream", key: "dev.ep.stream" },
  { method: "GET", path: "/api/v1/health", key: "dev.ep.health" },
] as const;

type Language = "curl" | "python" | "typescript";
const LANGUAGES: { id: Language; label: string }[] = [
  { id: "curl", label: "curl" },
  { id: "python", label: "Python" },
  { id: "typescript", label: "TypeScript" },
];

function examples(base: string): Record<Language, { id: string; title: string; code: string }[]> {
  return {
    curl: [
      {
        id: "list",
        title: "dev.examples.list",
        code: `curl ${base}/api/v1/projects/ \\
  -H "X-API-Key: $DOCMINDS_API_KEY"`,
      },
      {
        id: "upload",
        title: "dev.examples.upload",
        code: `curl ${base}/api/v1/documents/upload \\
  -H "X-API-Key: $DOCMINDS_API_KEY" \\
  -F project_id=$PROJECT_ID \\
  -F files=@handbook.pdf`,
      },
      {
        id: "search",
        title: "dev.examples.search",
        code: `curl ${base}/api/v1/search/ \\
  -H "X-API-Key: $DOCMINDS_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "annual leave", "project_id": "'$PROJECT_ID'", "top_k": 5}'`,
      },
      {
        id: "ask",
        title: "dev.examples.ask",
        code: `SESSION_ID=$(curl -s ${base}/api/v1/chat/sessions \\
  -H "X-API-Key: $DOCMINDS_API_KEY" -H "Content-Type: application/json" \\
  -d '{"project_id": "'$PROJECT_ID'"}' | jq -r .id)

curl ${base}/api/v1/chat/sessions/$SESSION_ID/messages \\
  -H "X-API-Key: $DOCMINDS_API_KEY" -H "Content-Type: application/json" \\
  -d '{"message": "How many days of leave do I get?"}'`,
      },
    ],
    python: [
      {
        id: "list",
        title: "dev.examples.list",
        code: `import os, requests

api = requests.Session()
api.headers["X-API-Key"] = os.environ["DOCMINDS_API_KEY"]
for project in api.get("${base}/api/v1/projects/").json():
    print(project["id"], project["name"])`,
      },
      {
        id: "upload",
        title: "dev.examples.upload",
        code: `with open("handbook.pdf", "rb") as f:
    documents = api.post("${base}/api/v1/documents/upload",
                         data={"project_id": project_id}, files={"files": f}).json()
print(documents[0]["status"])  # "pending" until the worker finishes`,
      },
      {
        id: "search",
        title: "dev.examples.search",
        code: `body = {"query": "annual leave", "project_id": project_id, "top_k": 5}
for hit in api.post("${base}/api/v1/search/", json=body).json()["results"]:
    print(round(hit["score"], 2), hit["filename"], hit["page_number"])`,
      },
      {
        id: "ask",
        title: "dev.examples.ask",
        code: `session = api.post("${base}/api/v1/chat/sessions", json={"project_id": project_id}).json()
reply = api.post(f"${base}/api/v1/chat/sessions/{session['id']}/messages",
                 json={"message": "How many days of leave do I get?"}).json()
print(reply["assistant_message"]["content"])`,
      },
    ],
    typescript: [
      {
        id: "list",
        title: "dev.examples.list",
        code: `const api = (path: string, init: RequestInit = {}) =>
  fetch("${base}/api/v1" + path, {
    ...init,
    headers: { "X-API-Key": process.env.DOCMINDS_API_KEY!, ...init.headers },
  }).then((r) => r.json());

const projects = await api("/projects/");
console.log(projects.map((p: { name: string }) => p.name));`,
      },
      {
        id: "upload",
        title: "dev.examples.upload",
        code: `import { readFile } from "node:fs/promises";

const form = new FormData();
form.append("project_id", projectId);
form.append("files", new Blob([await readFile("handbook.pdf")]), "handbook.pdf");
const [doc] = await api("/documents/upload", { method: "POST", body: form });
console.log(doc.status); // "pending" until the worker finishes`,
      },
      {
        id: "search",
        title: "dev.examples.search",
        code: `const json = { "Content-Type": "application/json" };
const { results } = await api("/search/", {
  method: "POST", headers: json,
  body: JSON.stringify({ query: "annual leave", project_id: projectId, top_k: 5 }),
});`,
      },
      {
        id: "ask",
        title: "dev.examples.ask",
        code: `const session = await api("/chat/sessions", {
  method: "POST", headers: json, body: JSON.stringify({ project_id: projectId }),
});
const reply = await api(\`/chat/sessions/\${session.id}/messages\`, {
  method: "POST", headers: json, body: JSON.stringify({ message: "How many days of leave do I get?" }),
});
console.log(reply.assistant_message.content);`,
      },
    ],
  };
}

function CodeBlock({ code, name }: { code: string; name: string }) {
  const { t } = useT();
  const [copied, setCopied] = useState(false);
  return (
    <div className="relative min-w-0">
      <button
        type="button"
        className="btn-ghost absolute right-2 top-2 text-xs"
        aria-label={t("dev.copyLabel", { name })}
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(code);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
          } catch {
            /* the code is on screen to select by hand */
          }
        }}
      >
        {copied ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
        {copied ? t("dev.copied") : t("dev.copy")}
      </button>
      <pre
        className="overflow-x-auto rounded-xl p-4 pr-24 text-xs leading-relaxed"
        style={{ background: "var(--surface-2)", color: "var(--text-primary)" }}
      >
        <code>{code}</code>
      </pre>
    </div>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-heading`} className="glass min-w-0 space-y-4 p-5 sm:p-6">
      <h2 id={`${id}-heading`} className="text-xl font-semibold">
        {title}
      </h2>
      {children}
    </section>
  );
}

export default function DevelopersPage() {
  const { t } = useT();
  const [language, setLanguage] = useState<Language>("curl");
  const docsUrl = `${API_BASE}/docs`;
  const samples = examples(API_BASE)[language];
  const muted = { color: "var(--text-secondary)" };

  const toc = [
    { id: "keys", title: t("dev.keys.title") },
    { id: "auth", title: t("dev.auth.title") },
    { id: "limits", title: t("dev.limits.title") },
    { id: "endpoints", title: t("dev.endpoints.title") },
    { id: "examples", title: t("dev.examples.title") },
    { id: "errors", title: t("dev.errors.title") },
  ];

  return (
    <main id="main" className="mx-auto max-w-4xl space-y-6 px-4 py-10 sm:px-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link href="/" className="inline-flex items-center gap-1.5 text-sm" style={muted}>
          <ArrowLeft size={14} aria-hidden="true" />
          {t("dev.back")}
        </Link>
        <LanguageSwitcher />
      </div>

      <header className="space-y-3">
        <h1 className="text-gradient text-3xl font-bold sm:text-4xl">{t("dev.title")}</h1>
        <p className="max-w-2xl text-base leading-relaxed" style={muted}>
          {t("dev.lede")}
        </p>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span style={muted}>{t("dev.baseUrl")}:</span>
          <code className="break-all">{API_BASE}</code>
          <a href={docsUrl} target="_blank" rel="noopener noreferrer" className="btn-ghost text-xs">
            <ExternalLink size={13} aria-hidden="true" />
            {t("dev.reference")}
          </a>
        </div>
      </header>

      <nav aria-label={t("dev.toc")} className="text-sm">
        <ul className="flex flex-wrap gap-x-4 gap-y-1">
          {toc.map((item) => (
            <li key={item.id}>
              <a href={`#${item.id}`} className="underline" style={{ color: "var(--accent-hover)" }}>
                {item.title}
              </a>
            </li>
          ))}
        </ul>
      </nav>

      <Section id="keys" title={t("dev.keys.title")}>
        <p style={muted}>{t("dev.keys.body")}</p>
        <ol className="list-decimal space-y-1 pl-5 text-sm">
          <li>{t("dev.keys.step1")}</li>
          <li>{t("dev.keys.step2")}</li>
          <li>{t("dev.keys.step3")}</li>
        </ol>
        <p className="text-sm" style={muted}>
          {t("dev.keys.role")}
        </p>
        <Link href="/settings" className="btn-primary inline-flex text-sm">
          {t("dev.keys.open")}
        </Link>
      </Section>

      <Section id="auth" title={t("dev.auth.title")}>
        <p style={muted}>{rich(t("dev.auth.body"), { header: <code>X-API-Key</code> })}</p>
        <CodeBlock name="X-API-Key" code={`X-API-Key: dm_...`} />
        <p className="text-sm" style={muted}>
          {t("dev.auth.bearer")}
        </p>
        <ul className="space-y-1 text-sm">
          {(["401", "403", "404"] as const).map((code) => (
            <li key={code}>
              <code>{code}</code> <span style={muted}>— {t(`dev.auth.${code}`)}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section id="limits" title={t("dev.limits.title")}>
        <p style={muted}>{t("dev.limits.body")}</p>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead style={{ color: "var(--text-muted)" }}>
              <tr>
                <th className="py-2 pr-3 font-medium">{t("dev.limits.endpoint")}</th>
                <th className="py-2 pr-3 font-medium">{t("dev.limits.limit")}</th>
                <th className="py-2 font-medium">{t("dev.limits.per")}</th>
              </tr>
            </thead>
            <tbody>
              {LIMITS.map((row) => (
                <tr key={row.endpoint} className="border-t" style={{ borderColor: "var(--border-subtle)" }}>
                  <td className="py-2 pr-3">
                    <code className="break-all">{row.endpoint}</code>
                  </td>
                  <td className="whitespace-nowrap py-2 pr-3">{row.limit}</td>
                  <td className="py-2">{t(`dev.limits.${row.per}`)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-sm" style={muted}>
          {rich(t("dev.limits.headers"), {
            limit: <code>X-RateLimit-Limit</code>,
            remaining: <code>X-RateLimit-Remaining</code>,
            retry: <code>Retry-After</code>,
          })}
        </p>
        <p className="text-sm" style={muted}>
          {t("dev.limits.quota")}
        </p>
      </Section>

      <Section id="endpoints" title={t("dev.endpoints.title")}>
        <p style={muted}>
          {rich(t("dev.endpoints.body"), {
            link: (
              <a href={docsUrl} target="_blank" rel="noopener noreferrer" className="underline" style={{ color: "var(--accent-hover)" }}>
                {t("dev.reference")}
              </a>
            ),
          })}
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead style={{ color: "var(--text-muted)" }}>
              <tr>
                <th className="py-2 pr-3 font-medium">{t("dev.endpoints.method")}</th>
                <th className="py-2 pr-3 font-medium">{t("dev.endpoints.path")}</th>
                <th className="py-2 font-medium">{t("dev.endpoints.purpose")}</th>
              </tr>
            </thead>
            <tbody>
              {ENDPOINTS.map((ep) => (
                <tr key={ep.path} className="border-t align-top" style={{ borderColor: "var(--border-subtle)" }}>
                  <td className="whitespace-nowrap py-2 pr-3 font-mono text-xs">{ep.method}</td>
                  <td className="py-2 pr-3">
                    <code className="break-all text-xs">{ep.path}</code>
                  </td>
                  <td className="py-2" style={muted}>
                    {t(ep.key)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section id="examples" title={t("dev.examples.title")}>
        <p style={muted}>{t("dev.examples.body")}</p>
        <div role="group" aria-label={t("dev.examples.tabs")} className="flex flex-wrap gap-2">
          {LANGUAGES.map((lang) => (
            <button
              key={lang.id}
              type="button"
              aria-pressed={language === lang.id}
              className={language === lang.id ? "btn-primary text-xs" : "btn-ghost text-xs"}
              onClick={() => setLanguage(lang.id)}
            >
              {lang.label}
            </button>
          ))}
        </div>
        {samples.map((sample) => (
          <div key={sample.id} className="space-y-2">
            <h3 className="text-sm font-semibold">{t(sample.title)}</h3>
            <CodeBlock name={t(sample.title)} code={sample.code} />
          </div>
        ))}
      </Section>

      <Section id="errors" title={t("dev.errors.title")}>
        <p style={muted}>{t("dev.errors.body")}</p>
        <CodeBlock name="error" code={`{ "detail": "Rate limit exceeded. Try again in 12 seconds." }`} />
      </Section>
    </main>
  );
}
