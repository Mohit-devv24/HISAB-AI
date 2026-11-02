import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/features/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        foreground: "var(--foreground)",
        canvas: "#131315",
        sidebar: "#191919",
        card: {
          dark: "#202020",
          border: "#2F2F2F",
          light: "#FFFFFF",
        },
        udhaar: {
          bg: "rgba(239, 68, 68, 0.12)",
          text: "#F87171",
          solid: "#EF4444",
        },
        jama: {
          bg: "rgba(16, 185, 129, 0.12)",
          text: "#34D399",
          solid: "#10B981",
        },
        gold: {
          DEFAULT: "#D97706",
          light: "#F59E0B",
          dark: "#B45309",
        },
      },
      fontFamily: {
        sans: ["Plus Jakarta Sans", "system-ui", "sans-serif"],
        hindi: ["Noto Sans Devanagari", "sans-serif"],
      },
      boxShadow: {
        glow: "0 0 25px -5px rgba(217, 119, 6, 0.3)",
        card: "0 4px 20px -2px rgba(0, 0, 0, 0.5)",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

export default config;
