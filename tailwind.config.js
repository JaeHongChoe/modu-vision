/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./index.html",
    "./src/renderer/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Inspection Dark Steel Charcoal Design System
        chassis: {
          DEFAULT: '#0B0E14', // Main root window background
          panel: '#131822',   // Docked toolbars, sidebars, header/footer
          card: '#1A212E',    // Inner cards, canvas containers, list items
          border: '#2B3547',  // 1px precision hairline borders
          hover: '#222B3D',   // Surface hover highlight
          active: '#2A364D',  // Active / pressed surface
        },
        panel: '#131822',     // Direct alias for docked toolbars & sidebars
        card: '#1A212E',      // Direct alias for inner cards & widgets
        border: '#2B3547',    // Direct alias for 1px hairline border color
        
        // Industrial Engineering LED Annunciators
        annunciator: {
          pass: '#10B981',    // PASS / OK (Industrial Emerald)
          fail: '#EF4444',    // FAIL / NG (Industrial Crimson)
          standby: '#F59E0B', // STANDBY / WARN (Industrial Amber)
          running: '#3B82F6', // RUNNING / ACTIVE (Electric Cobalt)
          offline: '#4B5563', // OFFLINE / DISCONNECTED (Muted Steel Gray)
        },
        
        // Industrial Telemetry & Metric Tokens
        metric: {
          readout: '#F8FAFC', // Ultra-high contrast primary readout text
          dim: '#94A3B8',     // Secondary readout text
          unit: '#64748B',    // Measurement units (ms, FPS, px, μm, °)
        },

        // Backward compatibility preserves
        industrial: {
          900: '#0b0f19',
          850: '#0f172a',
          800: '#1e293b',
          700: '#334155',
          600: '#475569',
          500: '#64748b',
        },
        brand: {
          50: '#eff6ff',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#1d4ed8',
        },
        ok: '#10b981',
        ng: '#ef4444',
        accent: '#f59e0b',
      },
      borderColor: {
        DEFAULT: '#2B3547',
        chassis: '#2B3547',
        panel: '#2B3547',
        border: '#2B3547',
        card: '#2B3547',
      },
      fontFamily: {
        mono: [
          'JetBrains Mono',
          'ui-monospace',
          'SFMono-Regular',
          'Menlo',
          'Monaco',
          'Consolas',
          'Liberation Mono',
          'Courier New',
          'monospace',
        ],
        sans: [
          'Inter',
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'Roboto',
          'Helvetica Neue',
          'Arial',
          'sans-serif',
        ],
      },
      borderRadius: {
        industrial: '4px',
        panel: '6px',
      },
      transitionDuration: {
        75: '75ms',
        100: '100ms',
      },
    },
  },
  plugins: [],
};
