// Draws the chart an answer asks for.

"use client";

import { useId, useState } from "react";

import { useT } from "@/lib/i18n";

export type ChartType = "bar" | "line" | "pie";

export interface ChartSpec {
  type: ChartType;
  title?: string;
  labels: string[];
  series: { name: string; values: number[] }[];
  unit?: string;
}

const MAX_SERIES = 8;
const MAX_POINTS = 40;
const MAX_SLICES = 8;

export function parseChartSpec(source: string): ChartSpec | null {
  let raw: unknown;
  try {
    raw = JSON.parse(source);
  } catch {
    return null;
  }
  if (!raw || typeof raw !== "object") return null;
  const data = raw as Record<string, unknown>;
  const type = data.type;
  if (type !== "bar" && type !== "line" && type !== "pie") return null;
  if (!Array.isArray(data.labels) || !Array.isArray(data.series)) return null;
  const labels = data.labels.slice(0, MAX_POINTS).map((l) => String(l).slice(0, 60));
  if (labels.length === 0) return null;
  const series = data.series
    .slice(0, MAX_SERIES)
    .map((s, index) => {
      const entry = (s ?? {}) as Record<string, unknown>;
      const values = Array.isArray(entry.values) ? entry.values.slice(0, labels.length) : [];
      return {
        name: String(entry.name ?? `Series ${index + 1}`).slice(0, 60),
        values: values.map((v) => (typeof v === "number" ? v : Number(v))),
      };
    });
  if (series.length === 0) return null;
  if (series.some((s) => s.values.length !== labels.length || s.values.some((v) => !Number.isFinite(v)))) {
    return null;
  }
  if (type === "pie" && (series.length !== 1 || series[0].values.some((v) => v < 0))) return null;
  return {
    type,
    labels,
    series,
    title: typeof data.title === "string" ? data.title.slice(0, 120) : undefined,
    unit: typeof data.unit === "string" ? data.unit.slice(0, 12) : undefined,
  };
}

const color = (index: number) => `var(--series-${(index % MAX_SERIES) + 1})`;

function format(value: number, unit?: string): string {
  const text = Number.isInteger(value) ? value.toLocaleString() : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return unit ? (unit === "%" ? `${text}%` : `${text} ${unit}`) : text;
}

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 5, 10]) if (step * power >= value) return step * power;
  return 10 * power;
}

const WIDTH = 560;
const HEIGHT = 240;
const PAD = { top: 12, right: 12, bottom: 44, left: 48 };

interface Hover {
  x: number;
  y: number;
  text: string;
}

export default function AnswerChart({ source }: { source: string }) {
  const { t } = useT();
  const spec = parseChartSpec(source);
  const [hover, setHover] = useState<Hover | null>(null);
  const titleId = useId();

  if (!spec) {
    return (
      <div className="my-2 text-xs" style={{ color: "var(--text-muted)" }}>
        <p>{t("chart.invalid")}</p>
        <pre className="mt-1 overflow-x-auto rounded-lg p-2" style={{ background: "var(--surface-1)" }}>
          {source}
        </pre>
      </div>
    );
  }

  const show = (event: { clientX?: number; clientY?: number; currentTarget: Element }, text: string) => {
    const box = event.currentTarget.closest("svg")?.getBoundingClientRect();
    const target = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX ?? target.left + target.width / 2) - (box?.left ?? 0);
    const y = (event.clientY ?? target.top) - (box?.top ?? 0);
    setHover({ x, y, text });
  };
  const markProps = (text: string) => ({
    tabIndex: 0,
    role: "img" as const,
    "aria-label": text,
    onMouseMove: (e: React.MouseEvent<SVGElement>) => show(e, text),
    onFocus: (e: React.FocusEvent<SVGElement>) => show(e, text),
    onMouseLeave: () => setHover(null),
    onBlur: () => setHover(null),
  });

  return (
    <figure className="my-3 rounded-xl p-3" style={{ background: "var(--chart-surface)", border: "1px solid var(--border-subtle)" }}>
      {spec.title && (
        <figcaption id={titleId} className="mb-2 text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
          {spec.title}
        </figcaption>
      )}
      {spec.series.length > 1 && spec.type !== "pie" && <Legend names={spec.series.map((s) => s.name)} />}
      <div className="relative">
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="h-auto w-full"
          role="group"
          aria-labelledby={spec.title ? titleId : undefined}
          aria-label={spec.title ? undefined : spec.series.map((s) => s.name).join(", ")}
        >
          {spec.type === "pie" ? <Pie spec={spec} markProps={markProps} /> : <Cartesian spec={spec} markProps={markProps} />}
        </svg>
        {hover && (
          <div
            role="presentation"
            className="pointer-events-none absolute z-10 rounded-md px-2 py-1 text-xs shadow-lg"
            style={{
              left: Math.min(hover.x + 10, 9999),
              top: Math.max(hover.y - 32, 0),
              background: "var(--bg-elevated)",
              color: "var(--text-primary)",
              border: "1px solid var(--border-strong)",
              whiteSpace: "nowrap",
            }}
          >
            {hover.text}
          </div>
        )}
      </div>
      <details className="mt-2 text-xs" style={{ color: "var(--text-secondary)" }}>
        <summary className="cursor-pointer">{t("chart.showData")}</summary>
        <div className="answer-table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">{t("chart.label")}</th>
                {spec.series.map((s) => (
                  <th key={s.name} scope="col">
                    {s.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {spec.labels.map((label, i) => (
                <tr key={`${i}-${label}`}>
                  <th scope="row">{label}</th>
                  {spec.series.map((s) => (
                    <td key={s.name}>{format(s.values[i], spec.unit)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}

function Legend({ names }: { names: string[] }) {
  return (
    <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs" style={{ color: "var(--text-secondary)" }}>
      {names.map((name, i) => (
        <li key={name} className="flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: color(i) }} />
          {name}
        </li>
      ))}
    </ul>
  );
}

type MarkProps = (text: string) => Record<string, unknown>;

function Cartesian({ spec, markProps }: { spec: ChartSpec; markProps: MarkProps }) {
  const all = spec.series.flatMap((s) => s.values);
  const minValue = Math.min(0, ...all);
  const maxValue = niceMax(Math.max(...all, 0));
  const plotW = WIDTH - PAD.left - PAD.right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const y = (v: number) => PAD.top + plotH - ((v - minValue) / (maxValue - minValue || 1)) * plotH;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => minValue + f * (maxValue - minValue));
  const band = plotW / spec.labels.length;
  const labelEvery = spec.labels.length > 12 ? Math.ceil(spec.labels.length / 12) : 1;

  return (
    <g>
      {ticks.map((tick) => (
        <g key={tick}>
          <line x1={PAD.left} x2={WIDTH - PAD.right} y1={y(tick)} y2={y(tick)} stroke="var(--chart-grid)" />
          <text x={PAD.left - 6} y={y(tick) + 3} textAnchor="end" fontSize="10" fill="var(--text-muted)">
            {format(Math.round(tick * 100) / 100)}
          </text>
        </g>
      ))}
      {spec.labels.map((label, i) =>
        i % labelEvery === 0 ? (
          <text
            key={`${i}-${label}`}
            x={PAD.left + band * (i + 0.5)}
            y={HEIGHT - PAD.bottom + 16}
            textAnchor="middle"
            fontSize="10"
            fill="var(--text-secondary)"
          >
            {label.length > 14 ? `${label.slice(0, 13)}…` : label}
          </text>
        ) : null
      )}

      {spec.type === "bar"
        ? spec.series.map((series, si) => {
            const groupPad = band * 0.18;
            const barW = Math.max(2, (band - groupPad * 2 - 2 * (spec.series.length - 1)) / spec.series.length);
            return series.values.map((value, i) => {
              const x = PAD.left + band * i + groupPad + si * (barW + 2);
              const top = y(Math.max(value, 0));
              const height = Math.max(1, Math.abs(y(value) - y(0)));
              const r = Math.min(4, barW / 2, height);
              const d =
                value >= 0
                  ? `M${x},${top + height} V${top + r} Q${x},${top} ${x + r},${top} H${x + barW - r} Q${x + barW},${top} ${x + barW},${top + r} V${top + height} Z`
                  : `M${x},${y(0)} H${x + barW} V${y(0) + height - r} Q${x + barW},${y(0) + height} ${x + barW - r},${y(0) + height} H${x + r} Q${x},${y(0) + height} ${x},${y(0) + height - r} Z`;
              return (
                <path
                  key={`${si}-${i}`}
                  d={d}
                  fill={color(si)}
                  {...markProps(`${series.name}, ${spec.labels[i]}: ${format(value, spec.unit)}`)}
                />
              );
            });
          })
        : spec.series.map((series, si) => {
            const points = series.values.map((v, i) => [PAD.left + band * (i + 0.5), y(v)] as const);
            return (
              <g key={series.name}>
                <polyline
                  points={points.map((p) => p.join(",")).join(" ")}
                  fill="none"
                  stroke={color(si)}
                  strokeWidth={2}
                  strokeLinejoin="round"
                />
                {points.map(([px, py], i) => (
                  <circle
                    key={i}
                    cx={px}
                    cy={py}
                    r={4}
                    fill={color(si)}
                    stroke="var(--chart-surface)"
                    strokeWidth={2}
                    {...markProps(`${series.name}, ${spec.labels[i]}: ${format(series.values[i], spec.unit)}`)}
                  />
                ))}
              </g>
            );
          })}
      <line x1={PAD.left} x2={WIDTH - PAD.right} y1={y(0)} y2={y(0)} stroke="var(--border-strong)" />
    </g>
  );
}

function Pie({ spec, markProps }: { spec: ChartSpec; markProps: MarkProps }) {
  let entries = spec.labels.map((label, i) => ({ label, value: spec.series[0].values[i] }));
  if (entries.length > MAX_SLICES) {
    const sorted = [...entries].sort((a, b) => b.value - a.value);
    const rest = sorted.slice(MAX_SLICES - 1).reduce((sum, e) => sum + e.value, 0);
    entries = [...sorted.slice(0, MAX_SLICES - 1), { label: "Other", value: rest }];
  }
  const total = entries.reduce((sum, e) => sum + e.value, 0) || 1;
  const cx = 130;
  const cy = HEIGHT / 2;
  const outer = 100;
  const inner = 58;
  const starts = entries.map(
    (_e, i) => -Math.PI / 2 + (entries.slice(0, i).reduce((sum, e) => sum + e.value, 0) / total) * Math.PI * 2
  );
  const point = (r: number, a: number) => `${cx + r * Math.cos(a)},${cy + r * Math.sin(a)}`;

  return (
    <g>
      {entries.map((entry, i) => {
        const sweep = (entry.value / total) * Math.PI * 2;
        const start = starts[i];
        const end = start + sweep;
        const large = sweep > Math.PI ? 1 : 0;
        const share = Math.round((entry.value / total) * 100);
        const d =
          sweep >= Math.PI * 2 - 1e-6
            ? `M${cx - outer},${cy} a${outer},${outer} 0 1,0 ${outer * 2},0 a${outer},${outer} 0 1,0 ${-outer * 2},0 M${cx - inner},${cy} a${inner},${inner} 0 1,1 ${inner * 2},0 a${inner},${inner} 0 1,1 ${-inner * 2},0`
            : `M${point(outer, start)} A${outer},${outer} 0 ${large} 1 ${point(outer, end)} L${point(inner, end)} A${inner},${inner} 0 ${large} 0 ${point(inner, start)} Z`;
        return (
          <path
            key={`${i}-${entry.label}`}
            d={d}
            fill={color(i)}
            stroke="var(--chart-surface)"
            strokeWidth={2}
            fillRule="evenodd"
            {...markProps(`${entry.label}: ${format(entry.value, spec.unit)} (${share}%)`)}
          />
        );
      })}
      {entries.map((entry, i) => (
        <g key={`legend-${i}`} transform={`translate(270, ${24 + i * 24})`}>
          <rect width="10" height="10" rx="2" fill={color(i)} />
          <text x="16" y="9" fontSize="11" fill="var(--text-secondary)">
            {entry.label.length > 28 ? `${entry.label.slice(0, 27)}…` : entry.label} ·{" "}
            {Math.round((entry.value / total) * 100)}%
          </text>
        </g>
      ))}
    </g>
  );
}
