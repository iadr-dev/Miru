import type { ReactNode } from "react";

/**
 * The 15 companions as small flat portraits on a 64x64 canvas (`viewBox="0 0 64 64"`).
 *
 * These are illustrations, not UI chrome: each animal keeps its natural colours, so the colours
 * live here as a fixed art palette rather than as theme tokens (they must not change with the
 * light/dark theme, and they never mean "up" or "down"). Everything around the art - the badge,
 * ring, text - still uses the theme tokens. One consistent style: front-facing head, simple
 * shapes, two dark eyes with a highlight.
 */

const INK = "#241f1c";
const WHITE = "#f7f4ee";

function Eye({ x, y, r = 2.2 }: Readonly<{ x: number; y: number; r?: number }>): ReactNode {
  return (
    <>
      <circle cx={x} cy={y} r={r} fill={INK} />
      <circle cx={x + r * 0.35} cy={y - r * 0.35} r={r * 0.35} fill={WHITE} />
    </>
  );
}

function BlueMagpie(): ReactNode {
  return (
    <>
      <ellipse cx="32" cy="52" rx="17" ry="11" fill="#2f6fb3" />
      <path d="M22 54 L26 62 L30 55 Z M34 55 L38 62 L42 54 Z" fill="#7fb2e0" />
      <circle cx="32" cy="31" r="16" fill="#1f2630" />
      <polygon points="28,33 36,33 32,43" fill="#d8453a" />
      <circle cx="25" cy="28" r="3.4" fill={WHITE} />
      <circle cx="39" cy="28" r="3.4" fill={WHITE} />
      <circle cx="25.4" cy="28.2" r="1.7" fill={INK} />
      <circle cx="38.6" cy="28.2" r="1.7" fill={INK} />
      <path
        d="M22 16 Q32 8 42 16"
        fill="none"
        stroke="#2f6fb3"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </>
  );
}

function BlackBear(): ReactNode {
  return (
    <>
      <path d="M21 48 L32 63 L43 48 Z" fill={WHITE} />
      <circle cx="16" cy="20" r="8" fill="#2b2a31" />
      <circle cx="48" cy="20" r="8" fill="#2b2a31" />
      <circle cx="16" cy="20" r="4" fill="#5a5762" />
      <circle cx="48" cy="20" r="4" fill="#5a5762" />
      <circle cx="32" cy="35" r="19" fill="#2b2a31" />
      <ellipse cx="32" cy="43" rx="10" ry="8" fill="#cdb894" />
      <ellipse cx="32" cy="39.5" rx="4" ry="2.8" fill="#111" />
      <path
        d="M32 42 L32 46 M28.5 47 Q32 49.5 35.5 47"
        fill="none"
        stroke={INK}
        strokeWidth="1.4"
        strokeLinecap="round"
      />
      <Eye x={24.5} y={31} r={2.1} />
      <Eye x={39.5} y={31} r={2.1} />
    </>
  );
}

function LeopardCat(): ReactNode {
  return (
    <>
      <polygon points="15,26 19,9 31,19" fill="#c8a064" />
      <polygon points="49,26 45,9 33,19" fill="#c8a064" />
      <polygon points="19,22 21,14 27,19" fill="#e9c4a0" />
      <polygon points="45,22 43,14 37,19" fill="#e9c4a0" />
      <circle cx="32" cy="37" r="19" fill="#c8a064" />
      <path
        d="M28 20 L28 26 M32 18 L32 25 M36 20 L36 26"
        stroke="#5a3d1c"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <circle cx="19" cy="42" r="1.6" fill="#5a3d1c" />
      <circle cx="22" cy="47" r="1.4" fill="#5a3d1c" />
      <circle cx="45" cy="42" r="1.6" fill="#5a3d1c" />
      <circle cx="42" cy="47" r="1.4" fill="#5a3d1c" />
      <path d="M17 33 L22 35 M47 33 L42 35" stroke={WHITE} strokeWidth="2" strokeLinecap="round" />
      <ellipse cx="32" cy="46" rx="9" ry="6" fill="#f3e9d8" />
      <circle cx="25" cy="34" r="3.6" fill="#e8b24a" />
      <circle cx="39" cy="34" r="3.6" fill="#e8b24a" />
      <ellipse cx="25" cy="34" rx="1.2" ry="2.6" fill={INK} />
      <ellipse cx="39" cy="34" rx="1.2" ry="2.6" fill={INK} />
      <polygon points="29,41 35,41 32,44.5" fill="#d98a8a" />
      <path
        d="M32 44.5 L32 47 M29 48 Q32 50 35 48"
        fill="none"
        stroke={INK}
        strokeWidth="1.2"
        strokeLinecap="round"
      />
    </>
  );
}

function Spoonbill(): ReactNode {
  return (
    <>
      <ellipse cx="32" cy="53" rx="18" ry="10" fill={WHITE} />
      <circle cx="32" cy="29" r="16" fill={WHITE} />
      <path
        d="M22 14 Q27 8 30 14 M34 14 Q38 8 43 14"
        fill="none"
        stroke="#e5ded0"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <ellipse cx="32" cy="31" rx="11" ry="7" fill="#1d1d22" />
      <path d="M28.5 34 L35.5 34 L35.5 47 Q41 51 32 55 Q23 51 28.5 47 Z" fill="#2a2a30" />
      <circle cx="26.5" cy="30" r="1.6" fill="#e6b84a" />
      <circle cx="37.5" cy="30" r="1.6" fill="#e6b84a" />
      <circle cx="26.5" cy="30" r="0.7" fill={INK} />
      <circle cx="37.5" cy="30" r="0.7" fill={INK} />
    </>
  );
}

function MikadoPheasant(): ReactNode {
  return (
    <>
      <ellipse cx="32" cy="53" rx="18" ry="10" fill="#2c3a58" />
      <path d="M17 51 H47 M19 56 H45" stroke="#cfd6e6" strokeWidth="1.6" strokeLinecap="round" />
      <path d="M26 20 L28 9 L32 18 L36 9 L38 20" fill="#2c3a58" />
      <circle cx="32" cy="32" r="16" fill="#2c3a58" />
      <ellipse cx="32" cy="34" rx="10" ry="7" fill="#b32a2a" />
      <circle cx="26.5" cy="32" r="2.6" fill={WHITE} />
      <circle cx="37.5" cy="32" r="2.6" fill={WHITE} />
      <circle cx="26.7" cy="32.2" r="1.3" fill={INK} />
      <circle cx="37.3" cy="32.2" r="1.3" fill={INK} />
      <polygon points="29,37 35,37 32,43" fill="#d7b24a" />
    </>
  );
}

function MasuSalmon(): ReactNode {
  return (
    <>
      <polygon points="48,34 61,22 61,46" fill="#e0644a" />
      <path d="M26 24 Q32 16 40 24 Z" fill="#e0644a" />
      <ellipse cx="30" cy="34" rx="22" ry="12" fill="#f08a6c" />
      <ellipse cx="28" cy="39" rx="15" ry="5" fill="#f9cdb9" />
      <path
        d="M16 24 Q13 34 16 44"
        fill="none"
        stroke="#c9553d"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <ellipse cx="30" cy="31" rx="2" ry="3" fill="#8a3a4a" opacity="0.55" />
      <ellipse cx="37" cy="31" rx="2" ry="3" fill="#8a3a4a" opacity="0.55" />
      <ellipse cx="44" cy="32" rx="1.6" ry="2.6" fill="#8a3a4a" opacity="0.55" />
      <circle cx="21" cy="31" r="3.4" fill={WHITE} />
      <circle cx="21" cy="31" r="1.9" fill={INK} />
      <path
        d="M12 36 Q15 38 18 36"
        fill="none"
        stroke={INK}
        strokeWidth="1.2"
        strokeLinecap="round"
      />
    </>
  );
}

function SikaDeer(): ReactNode {
  return (
    <>
      <path
        d="M23 24 L18 8 M18 8 L11 5 M19.5 13 L26 8 M41 24 L46 8 M46 8 L53 5 M44.5 13 L38 8"
        fill="none"
        stroke="#6b4a2b"
        strokeWidth="3"
        strokeLinecap="round"
      />
      <ellipse cx="14" cy="30" rx="8" ry="4.5" transform="rotate(-25 14 30)" fill="#9a6a3f" />
      <ellipse cx="50" cy="30" rx="8" ry="4.5" transform="rotate(25 50 30)" fill="#9a6a3f" />
      <ellipse cx="14" cy="30" rx="4.5" ry="2" transform="rotate(-25 14 30)" fill="#d9a98a" />
      <ellipse cx="50" cy="30" rx="4.5" ry="2" transform="rotate(25 50 30)" fill="#d9a98a" />
      <path d="M17 32 Q32 22 47 32 Q46 56 32 58 Q18 56 17 32 Z" fill="#9a6a3f" />
      <circle cx="29" cy="30" r="1.1" fill="#f1e2c8" />
      <circle cx="35" cy="29" r="1.1" fill="#f1e2c8" />
      <circle cx="32" cy="34" r="1.1" fill="#f1e2c8" />
      <ellipse cx="32" cy="49" rx="9" ry="7" fill="#dcc29c" />
      <ellipse cx="32" cy="45" rx="3.4" ry="2.4" fill="#2b2420" />
      <Eye x={24.5} y={38} r={2.1} />
      <Eye x={39.5} y={38} r={2.1} />
    </>
  );
}

function FormosanMacaque(): ReactNode {
  return (
    <>
      <circle cx="13" cy="35" r="6" fill="#e2b79a" />
      <circle cx="51" cy="35" r="6" fill="#e2b79a" />
      <circle cx="32" cy="33" r="19" fill="#8a7968" />
      <path d="M18 27 Q32 19 46 27 Q32 30 18 27 Z" fill="#6f604f" />
      <ellipse cx="32" cy="39" rx="13" ry="14" fill="#e2b79a" />
      <ellipse cx="32" cy="45" rx="8" ry="6" fill="#efcdb4" />
      <Eye x={26} y={35} r={2.2} />
      <Eye x={38} y={35} r={2.2} />
      <path
        d="M22 31 Q26 29.5 29 31 M35 31 Q38 29.5 42 31"
        fill="none"
        stroke="#6f604f"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <circle cx="30" cy="43" r="0.9" fill={INK} />
      <circle cx="34" cy="43" r="0.9" fill={INK} />
      <path
        d="M28 48 Q32 51 36 48"
        fill="none"
        stroke={INK}
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </>
  );
}

function FormosanSerow(): ReactNode {
  return (
    <>
      <path
        d="M23 24 Q17 15 23 8 M41 24 Q47 15 41 8"
        fill="none"
        stroke="#2d2a28"
        strokeWidth="3.4"
        strokeLinecap="round"
      />
      <ellipse cx="12" cy="30" rx="8" ry="4" transform="rotate(-15 12 30)" fill="#6f6a64" />
      <ellipse cx="52" cy="30" rx="8" ry="4" transform="rotate(15 52 30)" fill="#6f6a64" />
      <ellipse cx="32" cy="40" rx="14" ry="18" fill="#6f6a64" />
      <path d="M32 22 L32 36" stroke="#3a3633" strokeWidth="5" strokeLinecap="round" />
      <ellipse cx="32" cy="53" rx="7" ry="4" fill={WHITE} />
      <ellipse cx="32" cy="49" rx="7" ry="6" fill="#a39d93" />
      <ellipse cx="32" cy="46.5" rx="3" ry="2" fill="#2b2420" />
      <Eye x={25} y={36} r={2.1} />
      <Eye x={39} y={36} r={2.1} />
    </>
  );
}

function Sparrow(): ReactNode {
  return (
    <>
      <circle cx="32" cy="34" r="19" fill="#a8764a" />
      <path d="M14 30 Q32 10 50 30 Q32 22 14 30 Z" fill="#7a4b2a" />
      <ellipse cx="21" cy="41" rx="7" ry="6" fill="#efe3d0" />
      <ellipse cx="43" cy="41" rx="7" ry="6" fill="#efe3d0" />
      <ellipse cx="32" cy="48" rx="6" ry="5" fill="#2a2a2a" />
      <polygon points="28.5,36 35.5,36 32,43" fill="#d9a441" />
      <Eye x={25.5} y={31} r={2} />
      <Eye x={38.5} y={31} r={2} />
    </>
  );
}

function BambooPartridge(): ReactNode {
  return (
    <>
      <circle cx="32" cy="35" r="19" fill="#8d7656" />
      <ellipse cx="32" cy="29" rx="13" ry="10" fill="#b6b8b4" />
      <ellipse cx="32" cy="45" rx="10" ry="7" fill="#c97a3e" />
      <circle cx="21" cy="47" r="1" fill="#5a4630" />
      <circle cx="25" cy="52" r="1" fill="#5a4630" />
      <circle cx="39" cy="52" r="1" fill="#5a4630" />
      <circle cx="43" cy="47" r="1" fill="#5a4630" />
      <path d="M19 28 H29 M35 28 H45" stroke="#3b3228" strokeWidth="1.8" strokeLinecap="round" />
      <polygon points="29,35 35,35 32,40" fill="#4a3f33" />
      <Eye x={25} y={28} r={1.9} />
      <Eye x={39} y={28} r={1.9} />
    </>
  );
}

function CrestedSerpentEagle(): ReactNode {
  return (
    <>
      <path d="M17 24 L22 6 L28 18 L32 4 L36 18 L42 6 L47 24 Z" fill="#2f2a26" />
      <circle cx="26" cy="12" r="1.2" fill={WHITE} />
      <circle cx="32" cy="10" r="1.2" fill={WHITE} />
      <circle cx="38" cy="12" r="1.2" fill={WHITE} />
      <circle cx="32" cy="35" r="18" fill="#7a5a3a" />
      <path
        d="M17 28 L27 32 M47 28 L37 32"
        stroke="#2f2a26"
        strokeWidth="3"
        strokeLinecap="round"
      />
      <circle cx="25" cy="33" r="4.2" fill="#f0c93a" />
      <circle cx="39" cy="33" r="4.2" fill="#f0c93a" />
      <circle cx="25" cy="33" r="1.9" fill={INK} />
      <circle cx="39" cy="33" r="1.9" fill={INK} />
      <path d="M27 40 H37 Q37 44 32 52 Q27 44 27 40 Z" fill="#e0b73c" />
      <path
        d="M32 46 Q35 50 32 53"
        fill="none"
        stroke="#7a5a2a"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </>
  );
}

function FlyingSquirrel(): ReactNode {
  return (
    <>
      <circle cx="17" cy="21" r="7" fill="#8c8f94" />
      <circle cx="47" cy="21" r="7" fill="#8c8f94" />
      <circle cx="17" cy="21" r="3.4" fill="#e8b3b3" />
      <circle cx="47" cy="21" r="3.4" fill="#e8b3b3" />
      <circle cx="32" cy="36" r="18" fill="#8c8f94" />
      <ellipse cx="32" cy="41" rx="12" ry="11" fill="#f1f1ee" />
      <ellipse cx="24.5" cy="34" rx="5.4" ry="5" fill="#4a4d52" />
      <ellipse cx="39.5" cy="34" rx="5.4" ry="5" fill="#4a4d52" />
      <circle cx="24.5" cy="34" r="3.6" fill={INK} />
      <circle cx="39.5" cy="34" r="3.6" fill={INK} />
      <circle cx="25.7" cy="32.8" r="1.2" fill={WHITE} />
      <circle cx="40.7" cy="32.8" r="1.2" fill={WHITE} />
      <ellipse cx="32" cy="42" rx="2.2" ry="1.6" fill="#d98a8a" />
      <path
        d="M32 43.5 V46 M29 47.5 Q32 49.5 35 47.5"
        fill="none"
        stroke={INK}
        strokeWidth="1.2"
        strokeLinecap="round"
      />
      <path
        d="M17 44 L25 44 M17 47 L25 46 M47 44 L39 44 M47 47 L39 46"
        stroke="#c9c9c4"
        strokeWidth="1"
        strokeLinecap="round"
      />
    </>
  );
}

function WildBoar(): ReactNode {
  return (
    <>
      <polygon points="15,27 6,13 24,19" fill="#4a463f" />
      <polygon points="49,27 58,13 40,19" fill="#4a463f" />
      <path d="M20 20 L22 11 L26 18 L30 9 L33 17 L38 10 L40 19 L44 20" fill="#2f2c27" />
      <circle cx="32" cy="37" r="19" fill="#5b5750" />
      <ellipse cx="32" cy="46" rx="12" ry="9" fill="#8a7e72" />
      <ellipse cx="28" cy="46" rx="2" ry="2.8" fill="#2a2420" />
      <ellipse cx="36" cy="46" rx="2" ry="2.8" fill="#2a2420" />
      <path
        d="M21 50 Q16 51 17 44 Q20 47 24 47 Z M43 50 Q48 51 47 44 Q44 47 40 47 Z"
        fill={WHITE}
      />
      <Eye x={24.5} y={32} r={1.9} />
      <Eye x={39.5} y={32} r={1.9} />
    </>
  );
}

function Pangolin(): ReactNode {
  const scales: ReactNode[] = [];
  for (let row = 0; row < 4; row += 1) {
    for (let col = 0; col < 5; col += 1) {
      const x = 17 + col * 7.5 + (row % 2 === 0 ? 0 : 3.7);
      const y = 21 + row * 6;
      scales.push(
        <path
          key={`${row}-${col}`}
          d={`M${x - 3.6} ${y} Q${x} ${y + 7} ${x + 3.6} ${y}`}
          fill="none"
          stroke="#6a5a44"
          strokeWidth="1.4"
          strokeLinecap="round"
        />,
      );
    }
  }
  return (
    <>
      <ellipse cx="32" cy="35" rx="19" ry="16" fill="#8a7a62" />
      {scales}
      <path d="M26 38 Q32 33 38 38 L35 60 Q32 63 29 60 Z" fill="#b8a688" />
      <ellipse cx="32" cy="58" rx="3.4" ry="2.4" fill="#4a3f33" />
      <Eye x={24.5} y={33} r={1.8} />
      <Eye x={39.5} y={33} r={1.8} />
    </>
  );
}

/** pet_id -> its portrait. A pet the build has no art for falls back to a paw in `PetAvatar`. */
export const PET_ART: Record<string, () => ReactNode> = {
  sisi_magpie: BlueMagpie,
  formosan_black_bear: BlackBear,
  leopard_cat: LeopardCat,
  spoonbill: Spoonbill,
  mikado_pheasant: MikadoPheasant,
  salmon: MasuSalmon,
  sika_deer: SikaDeer,
  formosan_macaque: FormosanMacaque,
  formosan_serow: FormosanSerow,
  mountain_sparrow: Sparrow,
  bamboo_partridge: BambooPartridge,
  crested_serpent_eagle: CrestedSerpentEagle,
  flying_squirrel: FlyingSquirrel,
  wild_boar: WildBoar,
  pangolin: Pangolin,
};
