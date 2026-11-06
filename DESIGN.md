## Direction & Layout
- Product: Atoms Forge — a chat-driven builder: one sentence → CRUD app → live preview → publish. Mood: a calm workshop with one hot signal (forge ember). Should feel like a precise craft tool, not a glossy AI dashboard; must not feel purple/blue-gradient, glassy, or card-heavy.
- Defining visual idea: a warm paper-white canvas with ink-black chrome and a single ember-orange accent used only for the primary action, active state and the "live" dot. First screen: a large serif-free grotesk headline and one oversized prompt field (70/30 asymmetric: prompt left, template list right).
- Visual reference: printed workshop manuals / Teenage Engineering-style ruled layouts — hairline rules instead of boxed cards.
- Shell: 56px icon+label left rail (ink #16130F), content max-width 1200 on home; workspace is full-bleed 3 columns: chat 380px | stage flexible. Breakpoints: ≥1024 desktop two-pane; <1024 stacked with a Chat/Stage segmented switch; rail collapses to bottom bar <768.
- Density: medium-high, tables and lists over cards. Section spacing 32px, list rows 44px.
- Do: hairline borders (1px #E4DDD0), generous left alignment, mono labels for system data. Don't: gradient backgrounds, glow, left-colored-border cards, centered hero stacks.

## Tokens
- Colors: paper #FAF7F0, surface #FFFFFF, sunk #F1ECE0, ink #16130F, text-2 #5E574B, text-3 #8C8473, rule #E4DDD0, ember (primary) #E8590C, ember-dark #C2410C, ember-soft #FFE8D6, ok #2F7D4F, warn #B7791F, danger #B42318. Ember covers ≤10% of any screen.
- Typography: headings "Bricolage Grotesque" 600/700 (tight, characterful); body "IBM Plex Sans" 400/500 15px/1.55; mono "JetBrains Mono" 12–13px for ids, costs, tool traces. Display 44/48, h2 24/30, h3 16/24, caption 12/16.
- Spacing 4/8/12/16/24/32/48. Radii: buttons/inputs 6px, panels 8px, pills 999px. No icon button circles except status dots.
- Elevation: none by default; borders only. Overlays (dialogs) use 0 12px 32px rgba(22,19,15,.18).

## Shared Patterns & States
- Rail item: icon + 11px label, active = ember bar 2px at left + text paper. Primary button: ember bg, white text, 6px radius, 40px height; secondary: transparent with ink border; ghost: text only. Outline buttons use transparent background.
- Chat bubbles: user = sunk bg right-aligned; assistant = no bg, left rule. Agent trace lines (intent / tool / verify) are mono 12px text-3 with a small ember dot.
- Tables: hairline rows, 44px, header in mono caption caps. Empty states: one sentence + one action. Loading: inline spinner + stage text; errors: danger text with retry.
- Motion: 150ms ease-out for hover/press; no entrance animations on primary content.
- Accessibility: tap targets ≥40px, contrast ≥4.5:1, visible 2px ember focus ring, icon buttons have aria-label.

## Media
- No photography: this is an operational tool. Visual identity comes from type, rules and the ember accent. No generated imagery; abstract gradients not used.
