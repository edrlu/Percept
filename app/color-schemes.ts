export type ColorSchemeId = "dark";

export type ColorTokens = Record<`--${string}`, string>;

export type ColorScheme = {
  id: ColorSchemeId;
  name: string;
  description: string;
  swatches: [string, string, string];
  tokens: ColorTokens;
  familyColors: [string, string, string, string];
};

// percept ships ONE definitive near-monochrome dark theme. These tokens are
// applied inline on the app shell and therefore win over the :root fallbacks in
// globals.css — keep the two in sync. The only saturated colour in the product
// lives in `familyColors` (the four cortical systems) and the brain heatmap.
export const COLOR_SCHEMES: Record<ColorSchemeId, ColorScheme> = {
  dark: {
    id: "dark",
    name: "percept dark",
    description: "Near-monochrome graphite. Colour is reserved for the brain and its systems.",
    swatches: ["#0A0A0C", "#1A1A1D", "#F6F6F7"],
    tokens: {
      "--ink": "#F6F6F7",
      "--muted": "#9A9AA2",
      "--ink-faint": "#8A8A92",

      "--app-bg": "#08080A",
      "--paper": "#0A0A0C",
      "--canvas": "#0A0A0C",
      "--surface": "#141416",
      "--surface-raised": "#1A1A1D",
      "--surface-hover": "#232328",
      "--control-bg": "#1A1A1D",
      "--control-hover": "#26262C",
      "--selection": "rgba(255,255,255,.06)",

      "--line": "rgba(255,255,255,.09)",
      "--line-strong": "rgba(255,255,255,.17)",
      "--hairline": "rgba(255,255,255,.09)",
      "--hairline-strong": "rgba(255,255,255,.17)",
      "--focus-ring": "rgba(255,255,255,.22)",
      "--overlay": "rgba(0,0,0,.62)",
      "--file-overlay": "rgba(0,0,0,.72)",
      "--scrim": "rgba(0,0,0,.6)",

      "--orange": "#F6F6F7",
      "--orange-soft": "#1E1E22",

      "--lime": "#8FBE7E",
      "--live-bg": "rgba(143,190,126,.12)",
      "--live-line": "rgba(143,190,126,.42)",
      "--live-text": "#A9D196",
      "--danger": "#C9605A",
      "--danger-text": "#E59089",
      "--danger-soft": "rgba(201,96,90,.14)",

      "--stage": "#060608",
      "--stage-line": "rgba(255,255,255,.08)",
      "--stage-text": "#E8E8EA",
      "--stage-muted": "#86868D",
      "--stage-grid": "rgba(255,255,255,.04)",
      "--stage-hot": "#F0A53C",
      "--stage-hot-glow": "rgba(240,165,60,.22)",

      "--chart-grid": "rgba(255,255,255,.07)",
      "--chart-line": "#F6F6F7",
      "--chart-fill": "#F6F6F7",
      "--time-line": "#F6F6F7",

      "--radius-control": "8px",
      "--radius-card": "12px",
      "--radius-panel": "14px",
      "--radius-modal": "16px",
    },
    familyColors: ["#E0A35C", "#E47A86", "#9AA6F2", "#5EC8B8"],
  },
};

export const DEFAULT_COLOR_SCHEME: ColorSchemeId = "dark";
