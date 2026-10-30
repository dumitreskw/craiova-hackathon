// One color per entity type, readable on both the dark and light ground.
// Red and the survey-yellow accent are reserved for verification state.
export const TYPE_COLORS: Record<string, string> = {
  Domain: "#5b9bf0",
  IPAddress: "#3fb8af",
  ASN: "#4c9a8e",
  Organization: "#b48ae8",
  Person: "#f0955a",
  Email: "#6cc56c",
  Username: "#e07bb5",
  Phone: "#7fc8f8",
  SocialProfile: "#9d8cf0",
  URL: "#8a97ad",
  Document: "#a3b18a",
  Certificate: "#6c7a93",
  Location: "#56c2e6",
  Breach: "#ff7aa2",
  Repository: "#f7a072",
  Vulnerability: "#ff8a65",
};

export const ENTITY_TYPES = Object.keys(TYPE_COLORS);

export function typeColor(type: string): string {
  return TYPE_COLORS[type] || "#8a97ad";
}

// Types hidden by default: certificate history floods the graph.
export const DEFAULT_HIDDEN = new Set(["Certificate"]);
