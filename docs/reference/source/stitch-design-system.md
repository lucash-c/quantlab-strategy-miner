---
name: QuantLab Strategy Miner
colors:
  surface: '#f8f9ff'
  surface-dim: '#cbdbf5'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dce9ff'
  surface-container-highest: '#d3e4fe'
  on-surface: '#0b1c30'
  on-surface-variant: '#474651'
  inverse-surface: '#213145'
  inverse-on-surface: '#eaf1ff'
  outline: '#777682'
  outline-variant: '#c8c5d3'
  surface-tint: '#5654a8'
  primary: '#1a146b'
  on-primary: '#ffffff'
  primary-container: '#312e81'
  on-primary-container: '#9c9af4'
  inverse-primary: '#c3c0ff'
  secondary: '#4b41e1'
  on-secondary: '#ffffff'
  secondary-container: '#645efb'
  on-secondary-container: '#fffbff'
  tertiary: '#002b1b'
  on-tertiary: '#ffffff'
  tertiary-container: '#00432c'
  on-tertiary-container: '#14ba82'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#e2dfff'
  primary-fixed-dim: '#c3c0ff'
  on-primary-fixed: '#100563'
  on-primary-fixed-variant: '#3e3c8f'
  secondary-fixed: '#e2dfff'
  secondary-fixed-dim: '#c3c0ff'
  on-secondary-fixed: '#0f0069'
  on-secondary-fixed-variant: '#3323cc'
  tertiary-fixed: '#6ffbbe'
  tertiary-fixed-dim: '#4edea3'
  on-tertiary-fixed: '#002113'
  on-tertiary-fixed-variant: '#005236'
  background: '#f8f9ff'
  on-background: '#0b1c30'
  surface-variant: '#d3e4fe'
typography:
  headline-xl:
    fontFamily: Space Grotesk
    fontSize: 40px
    fontWeight: '600'
    lineHeight: 48px
    letterSpacing: -0.03em
  headline-xl-mobile:
    fontFamily: Space Grotesk
    fontSize: 28px
    fontWeight: '600'
    lineHeight: 36px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Space Grotesk
    fontSize: 28px
    fontWeight: '600'
    lineHeight: 36px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Space Grotesk
    fontSize: 20px
    fontWeight: '500'
    lineHeight: 28px
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Geist
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Geist
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-sm:
    fontFamily: Geist
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  label-md:
    fontFamily: Geist
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 18px
    letterSpacing: 0.01em
  label-sm:
    fontFamily: Geist
    fontSize: 11px
    fontWeight: '600'
    lineHeight: 14px
    letterSpacing: 0.05em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  gutter: 1.5rem
  gutter-mobile: 1rem
  margin: 2.5rem
  margin-mobile: 1rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 1rem
  space-lg: 1.5rem
  space-xl: 2.5rem
---

## Brand & Style

This design system embodies an avant-garde quantitative research laboratory: surgical, ultra-precise, and unburdened by legacy terminal noise. Tailored for quantitative researchers, algorithmic traders, and portfolio architects, the visual narrative shifts away from chaotic, hyper-dense trading screens toward a pristine, hyper-focused analytical atmosphere. 

The aesthetic is characterized by expansive, calm slate backdrops, surgical typography, razor-sharp technical structures, and structured breathing room. Every element conveys mathematical rigor, execution speed, and calm mastery over volatile market data.

## Colors

The palette leverages an authoritative midnight indigo base balanced against serene slate neutrals and deliberate tactical accents:

- **Primary (`#312E81`)**: Deepest midnight indigo. Anchor for high-authority headers, active states, and structural weight.
- **Secondary (`#4F46E5`)**: Vibrant computational indigo. Used for primary calls-to-action, active stepper indicators, and interactive focal points.
- **Tertiary (`#10B981`)**: Precision emerald. Reserved strictly for positive alpha, strategy approvals, execution validations, and yield indicators.
- **Neutral (`#64748B`)**: Balanced slate gray for metric labels, inactive states, and analytical annotations.
- **Surfaces & Backgrounds**: Base canvas rests at `#F8FAFC`, with core analytical modules and cards elevated on crisp `#FFFFFF`. Subtle containment lines use crisp `#E2E8F0` borders.

## Typography

The typographic hierarchy pairs the geometric and technical flair of Space Grotesk for quantitative titles with the clean, neutral precision of Geist for body and analytical readouts:

- **Display & Headlines (`Space Grotesk`)**: Engineered for strategy titles, financial metrics, and algorithmic state headers. Tightly tracked to deliver an authoritative, cutting-edge quantitative lab presence.
- **Body & Data Readouts (`Geist`)**: Delivers supreme optical clarity across multi-column data structures, parameter inputs, backtest metrics, and wizard flows without visual fatigue.
- **Numbers & Metrics**: Tabular numeric settings must be enabled by default across all financial readouts (`font-feature-settings: 'tnum' 1`) to guarantee column alignment.

## Layout & Spacing

A structured 12-column grid provides responsive elasticity across high-resolution desktop environments. Generous margins and breathing room isolate mission-critical parameters, preventing sensory overload.

- **Grid Architecture**: 12-column fluid grid system pinned with maximum content bounds (`1440px`). Gutters scale from `1rem` on compact displays to `1.5rem` on desktop displays.
- **Rhythm**: Rhythm is anchored to a strict 4px/8px incremental rhythm, prioritizing large, airy modules over cramped multi-pane dashboards.
- **Breakpoints**: 
  - Desktop (`≥ 1280px`): Full multi-panel layout, wide visual workspace, and side-by-side strategy miners.
  - Tablet (`768px – 1279px`): 8-column layout with stacked parameter forms and responsive charting.
  - Mobile (`< 768px`): 4-column flow with single-column linear wizards and collapsed metric ribbons.

## Elevation & Depth

Visual hierarchy rejects exaggerated, cloudy drop shadows in favor of razor-sharp containment, subtle borders, and shallow ambient luminescence:

- **Base Layering**: The primary backdrop sits at `#F8FAFC`. Card surfaces sit at `#FFFFFF`, outlined by crisp 1px borders (`#E2E8F0`).
- **Ambient Elevation**: 
  - Resting Cards: `0 1px 3px 0 rgba(15, 23, 42, 0.05)`.
  - Floating Overlays & Modals: `0 12px 32px -4px rgba(15, 23, 42, 0.08), 0 4px 12px -2px rgba(15, 23, 42, 0.03)`.
- **Focused State**: Focus borders trigger a high-precision `0 0 0 1px #4F46E5` accent with an outer glow of `0 0 0 3px rgba(79, 70, 229, 0.15)`.

## Shapes

The geometric framework favors tight, disciplined corners (`0.25rem` / `4px`), delivering a crisp instrument aesthetic:

- **Base Corner Radius**: `0.25rem` (4px) applied systematically to buttons, input fields, badges, and tabs.
- **Containers & Cards**: `rounded-lg` (`0.5rem` / 8px) on analytical cards, parameter modules, and data tables to preserve a modern structural feel.
- **Modals & Flyouts**: `rounded-xl` (`0.75rem` / 12px) for overlay dialogs and complex step wizards.

## Components

### Buttons
- **Primary**: Solid `#4F46E5` background with white text, 4px border radius, 40px height for standard actions. Hover transitions to `#4338CA`.
- **Secondary / Ghost**: Pure `#FFFFFF` surface with 1px `#E2E8F0` border and `#312E81` label. Hover transitions surface to `#F8FAFC` and border to `#CBD5E1`.
- **Destructive / Stop**: Crisp light tint `#FEF2F2` with `#DC2626` text and border for emergency stops or invalidations.

### Chips & Strategy Status Badges
- Compact 22px height, 4px corner radius.
- **Active / Mining**: `#ECFDF5` background, `#059669` text, `#A7F3D0` border with an optional pulsing dot.
- **Idle / Backtest**: `#EEF2FF` background, `#4F46E5` text, `#C7D2FE` border.
- **Neutral / Draft**: `#F1F5F9` background, `#475569` text, `#E2E8F0` border.

### Input Fields & Selectors
- Flat `#FFFFFF` interior with sharp 1px `#E2E8F0` borders and 4px radius. 
- Placeholder text set to `#94A3B8`. Numeric inputs feature monospaced formatting with integrated unit chips (e.g., `ms`, `ticks`, `%`, `USD`).
- Active focus displays a `#4F46E5` hairline border and subtle `rgba(79, 70, 229, 0.12)` ambient halo.

### Checkboxes & Radios
- Micro-square checks with 2px corners, defaulting to `#E2E8F0` border; when selected, filled with `#4F46E5` and a crisp white tick.

### Cards & Analytical Modules
- Generously padded (`1.5rem` to `2rem`) `#FFFFFF` containers with hairline `#E2E8F0` borders and soft structural headers.
- Modular layout: separated header with Space Grotesk metadata, clear metric callouts, and spacious interactive charting bodies.

### Step Wizards
- Clean horizontal timeline spanning top surfaces. Inactive stages use light slate dots and labels; completed stages transition to `#10B981` with tick marks; the active stage is highlighted with `#4F46E5` and an elevated numerical badge.

