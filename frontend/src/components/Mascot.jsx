/**
 * Swarm mascot "Bee" — reacts to connection, demo mode, and bot activity.
 */
export default function Mascot({ wsStatus = "offline", demoMode = false, botStatus = "idle", working = false }) {
  const mood = (() => {
    if (demoMode) return "demo";
    if (working || botStatus === "working") return "busy";
    if (botStatus === "needs_approval") return "alert";
    if (wsStatus === "online") return "happy";
    if (wsStatus === "connecting") return "wake";
    return "sleep";
  })();

  const bubble = (() => {
    if (demoMode) return "Demo mode — mock replies";
    if (working || botStatus === "working") return "Thinking…";
    if (botStatus === "needs_approval") return "Needs your OK";
    if (wsStatus === "online") return "Online & listening";
    if (wsStatus === "connecting") return "Connecting…";
    return "Offline — zzz";
  })();

  return (
    <div className={`mascot mascot-${mood}`} role="img" aria-label={`Swarm mascot, ${bubble}`}>
      <div className="mascot-bubble">{bubble}</div>
      <svg className="mascot-svg" viewBox="0 0 120 120" aria-hidden="true">
        <ellipse className="mascot-shadow" cx="60" cy="108" rx="28" ry="6" />
        <g className="mascot-wings">
          <ellipse className="wing wing-l" cx="38" cy="52" rx="18" ry="24" />
          <ellipse className="wing wing-r" cx="82" cy="52" rx="18" ry="24" />
        </g>
        <ellipse className="mascot-body" cx="60" cy="62" rx="26" ry="30" />
        <path className="mascot-stripe" d="M36 58 Q60 52 84 58" />
        <path className="mascot-stripe" d="M36 68 Q60 62 84 68" />
        <path className="mascot-stripe" d="M36 78 Q60 72 84 78" />
        <circle className="mascot-head" cx="60" cy="38" r="22" />
        <circle className="mascot-eye eye-l" cx="52" cy="36" r="4" />
        <circle className="mascot-eye eye-r" cx="68" cy="36" r="4" />
        <circle className="mascot-pupil pupil-l" cx="53" cy="36" r="1.8" />
        <circle className="mascot-pupil pupil-r" cx="69" cy="36" r="1.8" />
        <path className="mascot-smile" d="M52 44 Q60 50 68 44" />
        <path className="mascot-antenna" d="M52 18 Q48 8 44 4" />
        <path className="mascot-antenna" d="M68 18 Q72 8 76 4" />
        <circle className="mascot-tip" cx="44" cy="4" r="3" />
        <circle className="mascot-tip" cx="76" cy="4" r="3" />
        {mood === "busy" && (
          <g className="mascot-sparkles">
            <circle cx="22" cy="30" r="2" />
            <circle cx="98" cy="28" r="2.5" />
            <circle cx="18" cy="70" r="1.8" />
          </g>
        )}
      </svg>
      <div className="mascot-name">Bee</div>
    </div>
  );
}
