// A small day-by-day bar chart for the admin dashboard: one series, or two shown side by...

"use client";

import { useId, useState } from "react";

export interface Series {
  key: string;
  label: string;
  colour: string;
}

export default function TrendChart({
  title,
  rows,
  series,
  format = (n: number) => n.toLocaleString(),
  dateLabel,
  tableLabel,
}: {
  title: string;
  rows: Array<Record<string, number | string>>;
  series: Series[];
  format?: (n: number) => string;
  dateLabel: string;
  tableLabel: string;
}) {
  const id = useId();
  const [active, setActive] = useState<number | null>(null);
  const width = 600;
  const height = 160;
  const pad = { top: 8, bottom: 20, left: 4, right: 4 };
  const plotH = height - pad.top - pad.bottom;
  const max = Math.max(1, ...rows.flatMap((r) => series.map((s) => Number(r[s.key]) || 0)));
  const slot = (width - pad.left - pad.right) / Math.max(1, rows.length);
  const gap = 2;
  const barW = Math.max(1, (slot - gap * (series.length + 1)) / series.length);
  const shown = active === null ? null : rows[active];

  return (
    <figure className="glass p-4" aria-labelledby={`${id}-title`}>
      <figcaption id={`${id}-title`} className="mb-1 text-sm font-medium">
        {title}
      </figcaption>
      {series.length > 1 && (
        <ul className="mb-2 flex flex-wrap gap-4 text-xs" style={{ color: "var(--text-secondary)" }}>
          {series.map((s) => (
            <li key={s.key} className="flex items-center gap-1.5">
              <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: s.colour }} />
              {s.label}
            </li>
          ))}
        </ul>
      )}
      <p className="h-5 text-xs" style={{ color: "var(--text-secondary)" }} aria-live="polite">
        {shown
          ? `${shown.date}: ${series.map((s) => `${s.label} ${format(Number(shown[s.key]) || 0)}`).join(" · ")}`
          : ""}
      </p>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-40 w-full"
        role="img"
        aria-label={`${title}. ${tableLabel}.`}
        preserveAspectRatio="none"
        onMouseLeave={() => setActive(null)}
      >
        <line x1={0} x2={width} y1={pad.top} y2={pad.top} stroke="var(--border-subtle)" strokeDasharray="2 4" />
        <line
          x1={0}
          x2={width}
          y1={height - pad.bottom}
          y2={height - pad.bottom}
          stroke="var(--border-strong)"
        />
        {rows.map((row, i) => {
          const x0 = pad.left + i * slot;
          return (
            <g key={String(row.date)}>
              {series.map((s, j) => {
                const value = Number(row[s.key]) || 0;
                const h = value === 0 ? 0 : Math.max(2, (value / max) * plotH);
                return (
                  <rect
                    key={s.key}
                    x={x0 + gap + j * (barW + gap)}
                    y={height - pad.bottom - h}
                    width={barW}
                    height={h}
                    rx={Math.min(4, barW / 2)}
                    fill={s.colour}
                    opacity={active === null || active === i ? 1 : 0.45}
                  />
                );
              })}
              <rect
                x={x0}
                y={0}
                width={slot}
                height={height}
                fill="transparent"
                onMouseEnter={() => setActive(i)}
                onFocus={() => setActive(i)}
                onBlur={() => setActive(null)}
                tabIndex={-1}
              >
                <title>{`${row.date}: ${series.map((s) => `${s.label} ${format(Number(row[s.key]) || 0)}`).join(", ")}`}</title>
              </rect>
            </g>
          );
        })}
        <text x={pad.left} y={height - 4} fontSize={10} fill="var(--text-muted)">
          {String(rows[0]?.date ?? "")}
        </text>
        <text x={width - pad.right} y={height - 4} fontSize={10} fill="var(--text-muted)" textAnchor="end">
          {String(rows[rows.length - 1]?.date ?? "")}
        </text>
        <text x={width - pad.right} y={pad.top + 10} fontSize={10} fill="var(--text-muted)" textAnchor="end">
          {format(max)}
        </text>
      </svg>
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-xs" style={{ color: "var(--text-secondary)" }}>
          {tableLabel}
        </summary>
        <div className="max-h-64 overflow-auto">
          <table className="mt-2 w-full text-left text-xs">
            <caption className="sr-only">{title}</caption>
            <thead style={{ color: "var(--text-muted)" }}>
              <tr>
                <th scope="col" className="py-1 font-medium">
                  {dateLabel}
                </th>
                {series.map((s) => (
                  <th key={s.key} scope="col" className="py-1 font-medium">
                    {s.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={String(row.date)} className="border-t" style={{ borderColor: "var(--border-subtle)" }}>
                  <th scope="row" className="py-1 font-normal">
                    {String(row.date)}
                  </th>
                  {series.map((s) => (
                    <td key={s.key} className="py-1">
                      {format(Number(row[s.key]) || 0)}
                    </td>
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
