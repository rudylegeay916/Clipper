"use client";

import {
  Radar,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  ResponsiveContainer,
  Legend,
  Tooltip,
} from "recharts";
import type { Racket } from "@/types/racket";
import { CRITERIA } from "@/data/criteria";

/* Radar des 7 critères comportementaux (estimés).
   Jusqu'à 3 raquettes comparées. Une table reste l'alternative accessible. */

export const RADAR_COLORS = ["#c6f24e", "#38bdf8", "#c084fc"];

export function RacketRadarChart({
  rackets,
  height = 340,
}: {
  rackets: Racket[];
  height?: number;
}) {
  const data = CRITERIA.map((c) => {
    const row: Record<string, string | number> = { criterion: c.short };
    rackets.forEach((r, i) => {
      const v = r.estimatedScores[c.key];
      if (v != null) row[`r${i}`] = v;
    });
    return row;
  });

  return (
    <ResponsiveContainer width="100%" height={height}>
      <RadarChart data={data} outerRadius="72%">
        <PolarGrid stroke="rgba(255,255,255,0.1)" />
        <PolarAngleAxis
          dataKey="criterion"
          tick={{ fill: "#9fb0c3", fontSize: 12 }}
        />
        <PolarRadiusAxis domain={[0, 10]} tick={false} axisLine={false} />
        {rackets.map((r, i) => (
          <Radar
            key={r.id}
            name={`${r.marque} ${r.modele}`}
            dataKey={`r${i}`}
            stroke={RADAR_COLORS[i % RADAR_COLORS.length]}
            fill={RADAR_COLORS[i % RADAR_COLORS.length]}
            fillOpacity={0.25}
            strokeWidth={2}
          />
        ))}
        <Tooltip
          contentStyle={{
            background: "#0f1422",
            border: "1px solid rgba(255,255,255,0.16)",
            borderRadius: 12,
            fontSize: 13,
          }}
          labelStyle={{ color: "#f7fafc" }}
        />
        {rackets.length > 1 && <Legend wrapperStyle={{ fontSize: 12, color: "#9fb0c3" }} />}
      </RadarChart>
    </ResponsiveContainer>
  );
}
