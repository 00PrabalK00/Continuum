# Hyperframes Composition Brief: Continuum — beside git

## Objective
A short launch-style brag video for Continuum, framed as the half of your project history git was never for.

## Output
- Composition directory: `brag-output-2026-08-04-131006/composition/`
- Rendered video: `brag-output-2026-08-04-131006/brag.mp4`
- Format: landscape — 1920x1080
- Duration: 20.6s

## Source Material
- Project root: `C:/Users/Prabal/continuum`
- Primary files read: `git log --oneline`, `continuum/core.py` (lines 198-200), `continuum/cli.py`, `README.md`, `continuum/ui/styles.css`
- Product name: Continuum
- Strongest claim: not a claim — `core.py` writes `.continuum/.gitignore` containing `*`
- Key UI to recreate: two equal terminal panels, then one centred terminal
- Copy that must appear verbatim:
  - `$ git log --oneline`
  - `6129334 Add log, diff and restore over the checkpoint history (#64)`
  - `205ab40 Publish the verified benchmark results (#66)`
  - `95f4d51 Install from GitHub rather than a registry`
  - `84c0647 Release 0.13.0`
  - `> what am I working on?`
  - `I don't have any context for this project.`
  - `Git keeps the history of your code.`
  - `$ continuum save "fixed the auth bug | next: test the retry logic"`
  - `Saved: fixed the auth bug`
  - `Next:  test the retry logic`  ← two spaces, as `cli.py` prints it
  - `.continuum/` + `current.md` / `latest_handoff.md` / `events.sqlite3` / `session_logs/` with their README comments
  - `Continuum keeps the working context around it.`
  - `$ continuum go`
  - `Handing off to codex (claude used last).`
  - `Task: fixed the auth bug` / `Next: test the retry logic`
  - `$ cat .continuum/.gitignore`
  - `*`
  - `It sits beside git, not inside it.`
  - `Continuum` / `Shared memory for AI coding agents.` / `MIT · github.com/00PrabalK00/Continuum`

The four commits are this repository's real history and must not be paraphrased or invented.

## Creative Direction
- Tone preset: `polished`
- Creative direction: the two halves of a project's history, side by side — git on the left, the working context on the right
- Interpretation: four scenes, long holds, one idea per scene, no bullet lists, no stat cards. Restraint is the choice — the analogy carries itself.
- Angle: the user's sentence is the spine, split across the film — `Git keeps the history of your code.` under Scene 1, `Continuum keeps the working context around it.` under Scene 2. Git is not the problem and Continuum is not a replacement; the left panel stays correct and untouched throughout. Close on `.continuum/.gitignore` = `*`: it sits beside git, not inside it.
- Hook: `git log --oneline` dumping real commits, next to an agent admitting it has no context.
- Outro / punchline: `cat .continuum/.gitignore` → `*`, then **It sits beside git, not inside it.**
- Avoid: generic SaaS language; abstract filler; stat cards (used in the previous run); any implication that Continuum *is* git, replaces git, or that git is deficient. The two are complements.

## Visual Identity
- Background `#0c1018`; panel `#121b28`; bar `#101620`; border `#243144`
- Text `#e7edf7`; muted `#94a3b8`; accent `#5272f2`; small accent text `#7c94f6`; success `#43c59e`
- Display font Inter; product font JetBrains Mono at **20px / 32px line height** — sized so the longest real commit subject (67 chars) and the `continuum save` command (65 chars) each fit one unwrapped line inside an 880px panel
- Two equal 880x644 panels at x=70 and x=970, top 130; annotation band below at y=810

## Storyboard
`brag-plan.md` is the creative contract. Scene summary:
1. **Two logs** — 5.805s — git log dumps real commits; agent has no context; `Git keeps the history of your code.`
2. **The other half** — 5.793s — `continuum save`, real echo, `.continuum/` tree on the beat grid; `Continuum keeps the working context around it.`
3. **Handoff** — 4.205s — `continuum go`, identity swap claude→codex, the same question answered correctly.
4. **Beside git** — 4.797s — `cat .continuum/.gitignore` → `*`, caption, name.

Reading floor: short labels ≥0.8s settled; the two annotation lines ≥1.5s; entrances 0.3-0.45s, fast-in then hold.

## Audio
- Audio role: low warm bed with sparse professional accents
- Music: `assets/music/happy-beats-business-moves-vol-9-by-ende-dot-app.mp3` (114.84 BPM), already in the project
- Music treatment: 0.18 flat from 0.0s, no swell, duck to 0.13 at 17.6s, fade to 0 across 19.3 → 20.6s
- Music cue guidance: preset JSON is in `assets/music/`. Strong-cue locks (3): git-log dump @ **1.598**, Scene 1→2 cut @ **5.805**, Scene 2→3 cut @ **11.598**. Beat grid: `.continuum/` header @ 6.862, filenames @ 7.396 / 7.918 / 8.441 / 8.964, Scene 2's line @ 9.497; correct answer @ 14.222; `*` @ 16.858; caption @ 17.380; wordmark @ 18.437.
- Audio-reactive treatment: subtle. RMS/bass drive `--glow` / `--glowa`, the accent halo on the panels only. Pre-extracted to `assets/audio-data.js`.
- Audio-coupled moments: four typed commands (keystrokes); printed lines (low tick); `.continuum/` filenames (one tick each); the correct answer @ 14.222 (`impactSoft_medium_002`); `*` @ 16.858 (`impactSoft_medium_001`); the wordmark (nothing).
- SFX: reuse the files already in `assets/sfx/` — they were selected for low high-frequency risk.

## Hyperframes Instructions
Load `hyperframes-core`, `hyperframes-animation`, `hyperframes-creative`, `hyperframes-keyframes`, `hyperframes-cli`. Do not enter the `hyperframes` entry-point intent interview.

Requirements:
- Show real product output and this repo's real git history.
- Keep all text readable; honour the reading floor.
- 20.6s total.
- One transition vocabulary: hard cut travelling LEFT (the film's current), cut mid-motion with mirrored `power4.in` / `power4.out`, partial travel ~230px.
- The left panel is a still reference after Scene 1 — that stillness is intentional, not an idle scene.
- Include the planned music/SFX layer and the subtle audio-reactive halo.
- Run `hyperframes check` before render — 0 errors required, contrast findings included.
