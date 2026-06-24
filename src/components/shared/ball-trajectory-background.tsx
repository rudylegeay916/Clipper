import { cn } from "@/lib/utils";

/* Fond décoratif : trajectoires de balle (SVG, animation dash discrète).
   Purement décoratif → aria-hidden. Respecte prefers-reduced-motion via CSS. */
export function BallTrajectoryBackground({ className }: { className?: string }) {
  const paths = [
    "M -50 120 Q 360 -40 760 180 T 1500 120",
    "M -50 320 Q 420 520 820 300 T 1500 360",
    "M -50 520 Q 380 360 780 560 T 1500 500",
  ];
  return (
    <div
      aria-hidden
      className={cn("pointer-events-none absolute inset-0 overflow-hidden", className)}
    >
      <svg
        className="size-full"
        viewBox="0 0 1440 640"
        preserveAspectRatio="xMidYMid slice"
        fill="none"
      >
        <defs>
          <linearGradient id="traj-volt" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="rgba(198,242,78,0)" />
            <stop offset="50%" stopColor="rgba(198,242,78,0.35)" />
            <stop offset="100%" stopColor="rgba(56,189,248,0.05)" />
          </linearGradient>
        </defs>
        {paths.map((d, i) => (
          <path
            key={d}
            d={d}
            stroke="url(#traj-volt)"
            strokeWidth={1.5}
            strokeDasharray="6 14"
            style={{
              animation: `trajectory-dash ${28 + i * 6}s linear infinite`,
              opacity: 0.6 - i * 0.12,
            }}
          />
        ))}
      </svg>
    </div>
  );
}
