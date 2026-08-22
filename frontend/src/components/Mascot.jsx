/**
 * Pip — a Grok-companion-style mascot. Idle breathe/blink/look,
 * think-orbit when a bot is working, sleep when offline.
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
    if (working || botStatus === "working") return "On it…";
    if (botStatus === "needs_approval") return "Needs your OK";
    if (wsStatus === "online") return "Listening";
    if (wsStatus === "connecting") return "Waking up…";
    return "Asleep";
  })();

  return (
    <div className={`mascot mascot-${mood}`} role="img" aria-label={`Pip, ${bubble}`}>
      <div className="mascot-bubble">{bubble}</div>
      <svg className="mascot-svg" viewBox="0 0 160 170" aria-hidden="true">
        <defs>
          <radialGradient id="pip-fur" cx="38%" cy="32%" r="70%">
            <stop offset="0%" stopColor="#e89268" />
            <stop offset="55%" stopColor="#c45c3e" />
            <stop offset="100%" stopColor="#8a3826" />
          </radialGradient>
          <radialGradient id="pip-ear" cx="40%" cy="30%" r="70%">
            <stop offset="0%" stopColor="#d97854" />
            <stop offset="100%" stopColor="#7a3020" />
          </radialGradient>
          <radialGradient id="pip-inner" cx="50%" cy="40%" r="60%">
            <stop offset="0%" stopColor="#f3c4b0" />
            <stop offset="100%" stopColor="#e08a72" />
          </radialGradient>
          <radialGradient id="pip-muzzle" cx="40%" cy="30%" r="70%">
            <stop offset="0%" stopColor="#fffaf4" />
            <stop offset="100%" stopColor="#e8d8c8" />
          </radialGradient>
          <radialGradient id="pip-patch" cx="40%" cy="35%" r="65%">
            <stop offset="0%" stopColor="#5a241c" />
            <stop offset="100%" stopColor="#2a1210" />
          </radialGradient>
          <radialGradient id="pip-eye" cx="35%" cy="30%" r="70%">
            <stop offset="0%" stopColor="#fff" />
            <stop offset="100%" stopColor="#d9d0c6" />
          </radialGradient>
          <radialGradient id="pip-glow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="rgba(230,0,35,0.35)" />
            <stop offset="100%" stopColor="rgba(230,0,35,0)" />
          </radialGradient>
          <filter id="pip-soft" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="0.6" />
          </filter>
        </defs>

        <ellipse className="mascot-shadow" cx="80" cy="158" rx="34" ry="7" />
        <circle className="mascot-halo" cx="80" cy="72" r="58" />
        <g className="mascot-orbit">
          <ellipse cx="80" cy="72" rx="54" ry="18" />
          <circle className="mascot-orbit-dot" cx="134" cy="72" r="3.2" />
        </g>

        <g className="mascot-figure">
          <ellipse className="mascot-body" cx="80" cy="122" rx="30" ry="24" fill="url(#pip-fur)" />
          <ellipse className="mascot-belly" cx="80" cy="128" rx="16" ry="12" fill="url(#pip-muzzle)" />

          <g className="mascot-ear ear-l">
            <ellipse cx="48" cy="40" rx="15" ry="20" fill="url(#pip-ear)" transform="rotate(-18 48 40)" />
            <ellipse cx="50" cy="42" rx="7" ry="10" fill="url(#pip-inner)" transform="rotate(-18 50 42)" />
          </g>
          <g className="mascot-ear ear-r">
            <ellipse cx="112" cy="40" rx="15" ry="20" fill="url(#pip-ear)" transform="rotate(18 112 40)" />
            <ellipse cx="110" cy="42" rx="7" ry="10" fill="url(#pip-inner)" transform="rotate(18 110 42)" />
          </g>

          <circle className="mascot-head" cx="80" cy="72" r="38" fill="url(#pip-fur)" />
          <ellipse className="mascot-patch" cx="62" cy="70" rx="14" ry="12" fill="url(#pip-patch)" />
          <ellipse className="mascot-patch" cx="98" cy="70" rx="14" ry="12" fill="url(#pip-patch)" />
          <ellipse className="mascot-muzzle" cx="80" cy="86" rx="20" ry="15" fill="url(#pip-muzzle)" />

          <g className="mascot-eye-wrap eye-l">
            <circle className="mascot-eye" cx="63" cy="70" r="8.5" fill="url(#pip-eye)" />
            <g className="mascot-pupils">
              <circle className="mascot-pupil" cx="64" cy="71" r="3.6" />
              <circle className="mascot-glint" cx="61.8" cy="68.4" r="1.6" />
            </g>
            <rect className="mascot-lid" x="54" y="61" width="18" height="18" rx="9" />
          </g>
          <g className="mascot-eye-wrap eye-r">
            <circle className="mascot-eye" cx="97" cy="70" r="8.5" fill="url(#pip-eye)" />
            <g className="mascot-pupils">
              <circle className="mascot-pupil" cx="98" cy="71" r="3.6" />
              <circle className="mascot-glint" cx="95.8" cy="68.4" r="1.6" />
            </g>
            <rect className="mascot-lid" x="88" y="61" width="18" height="18" rx="9" />
          </g>

          <ellipse className="mascot-nose" cx="80" cy="88" rx="5" ry="3.6" />
          <path className="mascot-smile" d="M72 96 Q80 102 88 96" />
          <circle className="mascot-blush" cx="54" cy="86" r="5" />
          <circle className="mascot-blush" cx="106" cy="86" r="5" />

          {mood === "busy" && (
            <g className="mascot-sparkles">
              <circle cx="28" cy="48" r="2.2" />
              <circle cx="132" cy="40" r="1.8" />
              <circle cx="24" cy="96" r="1.5" />
            </g>
          )}
          {mood === "sleep" && (
            <g className="mascot-zzz">
              <text x="118" y="42">z</text>
              <text x="128" y="28">z</text>
            </g>
          )}
        </g>
      </svg>
      <div className="mascot-name">Pip</div>
    </div>
  );
}
