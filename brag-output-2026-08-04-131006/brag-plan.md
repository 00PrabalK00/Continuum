# Brag Plan: Continuum — beside git

Run: `brag-output-2026-08-04-131006`. Second angle on the same product; the first run
(`brag-output/`) is the deadpan handoff cut and is not replaced.

## Inspection rubric (Step 1)

1. **What is the app?** Continuum records what an AI coding session was doing into plain files in your project, and hands that record to whichever AI you open next.
2. **Funniest / most impressive claim?** Not a claim — a fact from the source. `continuum/core.py:198-200` writes `.continuum/.gitignore` containing `*`. Continuum deliberately keeps itself out of your repo: it sits beside git rather than competing with it. That is the closing beat, and it is real.
3. **Visual hook?** Two terminals side by side: `git log --oneline` on the left, an AI session on the right. The comparison *is* the concept.
4. **What to show from the actual UI?** This repo's own `git log --oneline` output, verbatim. Then real CLI output: `continuum save`, the `.continuum/` tree from the README, `continuum go` → `Handing off to codex (claude used last).`, and `cat .continuum/.gitignore` → `*`.
5. **Shortest satisfying video?** ~20s. Four beats: git holds the code → the agent holds nothing → Continuum holds the working context around it → it lives beside git, not inside it.
6. **Tone?** Preset `polished`; direction: *version-control framing, two logs side by side.* Deliberately different from the first run's `deadpan`. The product isn't a joke and the analogy carries itself, so restraint is the choice: few scenes, long holds, no bullets.
7. **Audio?** Same track and the same proven cue map as run 1. Flat low bed, keystrokes, one soft hit on the correct answer, fade to silence. No swell.
8. **Share caption draft?** "Git keeps the history of your code. Continuum keeps the working context around it — plain files in your project, sitting beside git rather than inside it."
9. **User flow worth showing?** Yes: agent with no context → `continuum save` writes `.continuum/` → `continuum go` hands off → the next agent answers correctly. Closing beat: `cat .continuum/.gitignore`.

---

## What is this app?
Git keeps the history of your code. Continuum keeps the working context around it — what you were doing, what you decided, what comes next — in plain files, handed to whichever agent you open next.

## The angle
The user's own sentence is the spine of the film, split across its two halves: **Git keeps the history of your code.** lands under Scene 1, **Continuum keeps the working context around it.** lands under Scene 2.

Git is not the problem and Continuum is not a replacement for it. The video puts both on screen at once — a real `git log` doing its job perfectly on the left, an agent saying `I don't have any context for this project.` on the right — and then fills in the half git was never for, while the left panel just sits there, still correct, untouched.

The closer is the repo's own behaviour: Continuum writes `.continuum/.gitignore` containing `*`. It stays out of your repo on purpose. It sits beside git, not inside it.

## Hook (first 2-3 seconds)
`$ git log --oneline` types out and dumps this repo's real commits — including `Add log, diff and restore over the checkpoint history (#64)`. The code's memory, working perfectly, right there.

## Key moments (the middle)
- The right terminal asks the same thing a person would: `> what am I working on?` and gets `I don't have any context for this project.` Side by side with the git log, that lands without a word of copy.
- `continuum save` writes `.continuum/` — `current.md`, `latest_handoff.md`, `events.sqlite3`, `session_logs/`.
- `continuum go` hands to codex; the same question now returns `Task:` / `Next:`.

## Outro / punchline
`$ cat .continuum/.gitignore` → `*`, and one line: **It sits beside git, not inside it.** Then the name.

## User flow worth showing
1. An agent with no context answers uselessly.
2. `continuum save` records the task and next step into `.continuum/`.
3. `continuum go` opens a different agent; it answers correctly, unprompted.
4. `cat .continuum/.gitignore` shows `*` — the record stays local.

## Tone
- Preset: `polished`
- Creative direction: the two halves of a project's history, side by side — git on the left, the working context on the right
- Interpretation: 4 scenes, long holds, one idea per scene, no bullet lists, no stat cards (run 1 already used those). The left panel deliberately goes quiet after Scene 1 — a still reference the right panel is measured against. Transitions are the same single vocabulary throughout: a hard cut travelling LEFT, cut mid-motion.

## Format: landscape — 1920x1080
## Duration: 20.6s

## Visual identity (from the project)
- Background `#0c1018`, panel `#121b28`, border `#243144`
- Text `#e7edf7`, muted `#94a3b8`, accent `#5272f2` (small text uses `#7c94f6`), success `#43c59e`
- Display font Inter, product font JetBrains Mono at 20px / 32px line height — sized so this repo's longest real commit subject (67 characters) fits one unwrapped line
- Strongest visual element: two terminals of equal weight — git working exactly as intended, and the half of the story it was never meant to hold

## Share copy (draft)
Git keeps the history of your code. Continuum keeps the working context around it — plain files in your project, sitting beside git rather than inside it.

## Audio direction
- Role: low warm bed with sparse professional accents (same posture as run 1)
- Music: `happy-beats-business-moves-vol-9-by-ende-dot-app.mp3` (114.84 BPM)
- Music treatment: 0.18 flat from 0.0s, no swell, duck to 0.13 at 17.6s, fade to silence 19.3 → 20.6s
- Music cue guidance: bundled preset `assets/music/happy-beats-business-moves-vol-9-by-ende-dot-app.music-cues.json`. Strong-cue locks (3): git-log dump @ **1.598**, Scene 1→2 cut @ **5.805**, Scene 2→3 cut @ **11.598**. Beat grid: `.continuum/` header @ 6.862, the four filenames @ 7.396 / 7.918 / 8.441 / 8.964, Scene 2's line @ 9.497; the correct answer @ 14.222; `*` @ 16.858; the caption @ 17.380; the wordmark @ 18.437.
- Audio-reactive treatment: subtle — RMS/bass drive the accent halo on both panels only. No bars, no particles.
- SFX posture: sparse, motion-matched. Keystrokes under typed commands, one soft tick per printed line, one `impactSoft` hit on the correct answer and one on `*`. Nothing on the wordmark.
- Restraint rule: no riser, no whoosh, no music swell on the name.

## Storyboard

### Scene 1 — Two logs — 5.805s (0 → 5.805)
Two equal terminals. **Left** titled `git`: types `$ git log --oneline`, then dumps four of this repo's real commits at once (a real `git log` prints as a block, and a block clears the reading floor a staggered reveal would not). **Right** titled `claude`: types `> what am I working on?` and prints `I don't have any context for this project.` One line lands below both: **Git keeps the history of your code.**
Sequential/interaction: yes — left types then dumps; right types then prints; the annotation lands last.
Audio intent: quiet, matter-of-fact. Keystrokes only.
Audio-coupled idea: keystrokes on both typed commands; the git-log dump beat-locked to strongCue 1.598.
Music: low flat bed.
Transition mood: hard cut on strongCue 5.805 → Scene 2

### Scene 2 — The other half — 5.793s (5.805 → 11.598)
Left panel holds, untouched. Right types `$ continuum save "fixed the auth bug | next: test the retry logic"`; the real echo prints `Saved: fixed the auth bug` / `Next:  test the retry logic`; then `.continuum/` writes itself in with its four real entries and their README comments, arriving one per beat and holding as a set. Annotation: **Continuum keeps the working context around it.**
Sequential/interaction: yes — four filenames on the beat grid, full set held 1.6s afterwards.
Audio intent: workmanlike. One soft tick per file.
Audio-coupled idea: `.continuum/` header on strongCue 6.862; filenames on beats 7.396 / 7.918 / 8.441 / 8.964; the line lands on strongCue 9.497.
Music: unchanged.
Transition mood: hard cut on strongCue 11.598 → Scene 3

### Scene 3 — Handoff — 4.205s (11.598 → 15.803)
Right types `$ continuum go`; prints `Handing off to codex (claude used last).`; the panel's identity swaps `claude` → `codex` with a single accent line drawn under its title bar. The same question repeats, and this time the answer is right: `Task: fixed the auth bug` / `Next: test the retry logic` in `--success`. No annotation — the two panels have already made the point.
Sequential/interaction: yes — simulated agent switch; the repeated question is the whole argument.
Audio intent: one restrained hit on the correct answer, nothing else.
Audio-coupled idea: correct answer beat-locked to strongCue 14.222.
Music: unchanged.
Transition mood: hard cut on strongCue 15.803 → Scene 4

### Scene 4 — Beside git — 4.797s (15.803 → 20.600)
Both panels give way to a single centred terminal. Types `$ cat .continuum/.gitignore`. Output: `*`. Caption: **It sits beside git, not inside it.** Then **Continuum**, the tagline, and the repo line. Long quiet hold; music fades out under it.
Sequential/interaction: yes — typed command, then output, then caption, then the name.
Audio intent: one soft hit on `*`, then silence under the name.
Audio-coupled idea: `*` on beat 16.858; caption on beat 17.380; wordmark on beat 18.437.
Music: fades to zero across 19.3 → 20.6.
Transition mood: hold to end.

**Music mood for this video:** polished — low flat bed, no swell, fades out.
**Audio summary:** The same restrained bed as run 1, carrying keystrokes and low ticks, spending exactly two soft hits — one on the answer that proves the point, one on the `*` that closes it — then fading to nothing under the name.

---

## Final build notes

Locked boundaries (20.6s): `0 – 5.805` two logs · `5.805 – 11.598` the other half · `11.598 – 15.803` handoff · `15.803 – 20.600` beside git.

Scenes 1-3 are one clip — two panels held side by side the whole way, with a single hard cut LEFT into the outro. The left `git` panel is written once and never touched again; it stays correct while the right panel fills in.

Changes made during the build:
- **The git panel carries 16 real commits, not 4.** With four it read as empty, which undercut the whole comparison. Sixteen fills the panel, so the frame itself says "git has all of this, the other side has nothing" before a word of copy lands. All sixteen are this repository's real history, filtered to subjects that fit one unwrapped line at 20px mono.
- The `git log` output prints as a single block on strongCue 1.598 — that is what a real `git log` does, and it clears the reading floor a staggered reveal could not.
- Scene 3 carries no annotation line. `Continuum keeps the working context around it.` holds from 9.497 all the way to the cut at 15.803, so the handoff plays underneath the film's thesis rather than competing with a third line.

Beat work (marked in `index.html`): strong-cue locks at **1.598** (git log dump), **6.862** (`.continuum/`), **12.655** (handoff line) and **14.222** (the correct answer); Scene 1→2 and Scene 2→3 boundaries sit on strong cues **5.805** and **11.598**; beat grid for the four `.continuum/` filenames at 7.396 / 7.918 / 8.441 / 8.964; Scene 2's line lands on strong cue 9.497; `*` on beat 16.858, caption 17.380, wordmark 18.437.

Poster: t=15.0s — the two-panel frame with the thesis line. It states the whole idea and names the product in the same image.

Gate: `npx hyperframes check` — 0 errors, 0 warnings, **398/398** WCAG AA text checks pass.
