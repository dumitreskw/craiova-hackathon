/** @type {import('tailwindcss').Config} */
// Colors resolve to CSS variables (src/index.css) so light/dark share one class set.
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ground: v("ground"),
        panel: v("panel"),
        raised: v("raised"),
        line: v("line"),
        ink: v("ink"),
        muted: v("muted"),
        accent: v("accent"),
        "accent-ink": v("accent-ink"),
        "accent-text": v("accent-text"),
        danger: v("danger"),
        ok: v("ok"),
        warn: v("warn"),
        canvas: v("canvas"),
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', "system-ui", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "monospace"],
      },
      fontSize: {
        // Smallest size in the app: metadata only, never body copy.
        "2xs": ["12px", "16px"],
        xs: ["13px", "18px"],
        sm: ["14px", "20px"],
        base: ["15px", "23px"],
      },
      boxShadow: {
        float: "0 6px 24px -6px rgb(0 0 0 / 0.35), 0 1px 0 rgb(var(--line) / 0.6) inset",
      },
    },
  },
  plugins: [],
};
