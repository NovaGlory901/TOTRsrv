# Front end

Plain HTML, CSS and a little JavaScript. No framework, no bundler, no build step. The look is a dreamy CRT terminal: deep indigo
background, pastel pink/cyan/lilac glow, scanlines, a bezel, and the VT323 font. Fonts are served by the server (`static/fonts/fonts.css`, linked from every page): VT323 first, then Noto Sans and the Japanese, Chinese and Korean Noto fonts as fallbacks, cut into small pieces so a page downloads only what its text needs. Regenerate with `TOTRspa/TOTRspa_web/tools/make-fonts.cjs`.

## Files
| File | Role |
|---|---|
| `static/style.css` | The whole theme: CSS variables in `:root`, `.screen` (bezel/scanlines), cards, tabs, forms, the player bar |
| `static/app.js` | `window.apiFetch` (adds `X-CSRF-Token` to changing requests, redirects to `/login` on 401) and the sign-out button |
| `templates/*.html` | One file per page, each with `{{HEADER}}`, `{{FOOTER}}` and its own inline `<script>` |

## Pages
`index` (landing), `archive` (hash-routed library browser with search, mixtapes and a sticky player with autoplay), `radio` (collage engine), `settings` (also served at `/account`), `users`, `upload`,
`admin` (three tabs: status, maintenance, collector), `login`, `invite`.

## Archive routes (hash)
`#/` artists, `#/compilations`, `#/artist/7`, `#/artist/7/album/3`, `#/album/3`, `#/search/<text>` (the search box above the page types into this route; results are artists, albums and songs, songs play from the list),
`#/mixtapes` (yours; create one), `#/mixtape/4` (play, rename, delete, move songs up/down, remove). Every song row, "Add all" button and the player bar's `+` open a small dialog to pick a mixtape or start a new one.

## Rules (the CSP enforces them)
- **No inline event handlers** (`onclick="..."`). Assign `element.onclick = ...` in JavaScript.
- **Inline `<script>` blocks are fine** (the server adds a nonce), but **no `eval`**, no inline `style=` *attributes set through `setAttribute`*
  (assigning `element.style.x` or `cssText` is fine; static `style="..."` attributes and `<style>` blocks are allowed).
- **Never use `innerHTML` with data.** Build elements and set `textContent`. This applies double to anything from spiders or the collector (third-party text).
- **Links from data** must be `https` and on an allowed site before becoming clickable (see `link()` in `admin.html`).
- Load nothing from a new origin without updating `csp()` in `auth/web.py` (currently nothing but the server itself).
- Use `apiFetch` for every request to the app; the login and invite pages use plain `fetch` because they have no session yet.
- Media elements load same-origin URLs only (`media-src 'self'`).

## Conventions
- Headings get a cloud glyph via CSS (`main h1::before`); buttons share one style; `[hidden]` always wins (`display: none !important`).
- Status colours: `.ok` cyan, `.warn` yellow, `.bad` red, `.muted` lavender.
- Accessible by default: labelled inputs, keyboard-focusable cards (`tabindex`, Enter), `aria-current` on nav and tabs, `role="status"` on messages,
  `prefers-reduced-motion` turns the animations off.
- Polling: a page polls only while something is active and visible (for example downloads running), then stops.
- Admin tab modules are IIFEs exposing `start()`/`stop()`; `showTab()` starts the visible one and stops the rest.

## Archive routes (hash)
`#/` artists, `#/compilations`, `#/artist/7` (Albums, Singles = loose songs, All songs), `#/artist/7/album/3`, `#/album/3`. A route counter ignores stale responses
when you click quickly.

## Radio
Tune in (needed for browser autoplay rules), then `/api/radio/next` supplies random songs (recent ones excluded); on `ended` the next song starts;
five consecutive errors stop it with a message. The collage spawns layers (album covers, files from the Dream folder, fake "dream windows", floating
phrases, orbs) that drift with the Web Animations API; at most two videos at a time; fewer layers and no movement under reduced motion.

**Previous / Next.** `RadioCore.SongHistory` remembers the songs of this radio session (last 200). Prev restarts the song if it has played more than 3 seconds, otherwise goes back one;
Next after going back walks forward through the history first and only then asks the server for a new random song.

**DejaVuAmp** (the "DejaVuAmp" button on the radio page; TOTR's own player rack in the spirit of the classic desktop players, not a copy of any). Three standalone files, no page coupling, so the React app
can import the same ones. (The originals live in the DejaVuAmp and FalseHorizons repositories, in `js/`, except `falsehorizon-scene.js`, which TOTR owns here in `TOTRsrv/static/`; `static/` holds copies made by `tools/sync-vendor.cjs`, and `tests/test_vendor.py` fails if a copy was edited by hand.) The files: `static/dejavuamp-core.js` (pure logic, `window.DejaVuAmpCore`: dB maths, the 19 built-in equalizer presets, settings validation and storage, preset files, time and ticker text),
`static/dejavuamp.js` (`DejaVuAmp.create({ audio, mount, username, storage, calls })`: the audio graph and the player and equalizer windows) and `static/dejavuamp.css` (themed with the site's variables).
The graph is built inside the Tune-in click (browsers require it): `source -> preamp -> 10 filters (60 Hz low shelf, eight bells 170 Hz-14 kHz, 16 kHz high shelf) -> optional mono fold -> balance
(stereo panner) -> clip guard (a fast compressor just under full scale, switchable) -> analyser (2048 points, feeds FalseHorizon and the spectrum) -> volume -> speakers`. Without Web Audio, volume and
speed still work through the element. The player window has a time display (click: elapsed/remaining), a spectrum / oscilloscope / off display (click to cycle), a scrolling title, seek, previous/play/next,
volume (dB), balance (double-click to centre), MONO / EQ / LIMIT switches and speed (pitch kept). The equalizer window has a preset menu, your own named presets (up to 20), export and import of
`.dva-eq.json` preset files (ten numbers, clamped, 2 KB max), reset, and a preamp. Settings are per signed-in person in the browser (`dejavuamp.v1.<username>`), validated on the way back in
(`DejaVuAmpCore.sanitize`; the preset name shown is worked out from the sliders, never stored). The page works if storage is blocked. `rack.inspect()` reports the graph's shape and what it was last told (for tests).

**FalseHorizon** (the Visuals; TOTR's own preset-driven visualizer in the spirit of the classic feedback visualizers, original code, no one else's presets or art). Seven standalone files, none tied to the
radio page, so the React app can import them as they are: `static/falsehorizon-expr.js` (the equation language), `falsehorizon-core.js` (what a preset is and how a file is checked, the audio analysis, the
per-frame maths, the flash guard, picking presets, settings), `falsehorizon-presets.js` (the 17 built-in presets, plain data), `falsehorizon-milk.js` (the MilkDrop importer, below), `falsehorizon.js` (the WebGL 1 engine: `FalseHorizon.create(canvas)`) and
`falsehorizon-scene.js` (TOTR's own glue, not part of the FalseHorizons repository; `FalseHorizonScene.create(hostElement, { onFail })`: copies a page's backdrop and collage onto a 2D canvas each frame) and
`falsehorizon-ui.js` + `falsehorizon.css` (the controls: `FalseHorizonUI.create({ engine, mount, username, storage, calls })`). The visuals are the dominant layer: the radio page hands the engine a **scene** (`engine.setScene(source)`, `source = { canvas, update(w, h) -> bool }`) holding the vaporwave backdrop (turning colour wheel, stars, scrolling grid) and the collage's covers, video, words, windows and orbs, drawn at about two-thirds of the picture size. The engine stamps it into the feedback loop (`SCENE_AMT`) and shows a little of it unwarped (`SCENE_DIRECT`), so the visuals warp, smear and tear the page instead of sitting on it. The DOM collage stays in the page, hidden (`.stage.fh-on #layers { visibility: hidden }`), so its animations keep running; if drawing the scene fails three times the page shows it again as it was. A host other than the radio page only needs to give the scene a container whose children are positioned `.layer` elements (`img`, `video`, `.orb` with `data-hue`, `.phrase`, `.dwin`), or its own `{ canvas, update }`. Without a scene the engine works as before.
Every frame: (1) the previous picture is drawn again through a **warp mesh** (a 32 x 24 grid whose points say where each part of the new picture comes from in the old: zoom, spin, stretch, drift, ripple), faded a
hair, and a little of the current album cover is mixed in so it smears and trails with everything else; (2) the preset's waveforms and shapes are drawn on top; (3) the picture's average brightness goes to the
**flash guard**; (4) colour effects (gamma, hue, solarize, a mirrored echo) and the guard's dimming are applied and it is shown. Changing preset blends the two warps and fades their shapes over 2.5 seconds in the same
picture; in the middle of a change both warps act at once (`CROSS`), echo and trails build up, and both presets' shapes stay strong, so the two presets interfere instead of trading places. The visuals hear the music 20% louder than the analyser reports (`SENSITIVITY`, capped at 1; the beat detector uses the true level), the default intensity is 77, and like MilkDrop the picture keeps moving when the music is paused (it hears silence); it stops only when switched off, when the tab is hidden, or before the first Tune in. A **preset** is a small JSON file (`{"totr-visual": 1, "name": ..., "decay", "zoom", "rot", "warp", ..., "wave", "color", "init"/"frame"/"pixel" formulas, up to 2 custom waves and 4 shapes}`, at most 64 KB;
`FalseHorizonCore.sanitizePreset` clamps every number and checks every formula). **Formulas** (`falsehorizon-expr.js`) are our own language, not JavaScript: `zoom = 1.02 + 0.05 * bass;` with numbers, names,
`+ - * / % ^`, comparisons, `&& || !`, and a fixed list of functions (`sin`, `if`, `clamp`, `mix`, `rand` ...). No eval or `Function`: a formula is parsed into closures that only do arithmetic on a plain object of
numbers. No loops or strings; dividing by zero gives 0; results are clamped; hard limits (6000 characters, 250 statements, 120 names, 24 deep per formula; 30,000 characters per preset) refuse anything huge, and a worst-case preset is tested to compute a frame in well under 40 ms. Inputs a formula sees: `time`, `frame`, `bass`, `mid`, `treb`
(and slow `*_att` averages, so `bass - bass_att` is a punch), `level`, `beat` (1 on a beat, fading), `aspect`; outputs it sets (each clamped to a range): `zoom rot cx cy dx dy sx sy warp decay` and the wave and colour values; `pixel`
formulas run at every mesh point with `x y rad ang`. Drawn light is added at 40% strength (it accumulates through the feedback loop). Inside every preset's picture the engine also works **the fractal** (`FalseHorizonCore.fractalWarp`). It is part of the warp, not drawn over it (there is no overlay). Every mesh point gets a height `Z = (1 - m) * Z_base + m * Z_fractal`, where `m = (sin(time * 0.35) + 1) / 2` (`modulationIndex`, a round trip about every 18 s), `Z_base` is a travelling ring wave and `Z_fractal` is the Mandelbrot set's smooth escape count (`fractalHeight`). The surface is tipped and turned with perspective, and the difference between where each point of the picture is and where the surface puts it is added to the warp mesh each frame (about as strong as a preset's own warp), so the preset's picture, the page's covers, words and backdrop, and the trails are all pulled over it. As `m` nears 1 the constant `C` of the Mandelbrot sum is shaken by the music's waveform and by noise (cubed, so it builds late), so the surface tears right before it melts back into the clean wave. The surface is also pulled in toward the middle in proportion to `m` (the tunnel feel), and the height and camera were tuned 20% deeper on 2026-10-06 (`lift` 0.66, `CAM` 2.35). Calm mode is four times slower and shakes less. The amount is the *geometry* slider (default 60, 0 turns it off; `engine.setGeometry`). **Controls** (the Visuals block under DejaVuAmp): on, intensity, album art amount,
style (FalseHorizon or **Classic photo-smear**, the previous visualizer, `radio-viz.js`, built only if chosen), preset menu (Favourites / Built in / Yours), previous, next, lock, favourite, change-by-itself every 10-120 s,
and "Look inside, change or keep a preset": the preset as text you can change, try, save (up to 30 of your own; a built-in name is refused), delete, export and import as `.fh-preset.json`. The preset's name shows
on the stage for a few seconds. **Safety:** the **flash guard** (`FlashGuard`) dims the picture so its brightness never exceeds 0.45 (0.35 in calm mode) and never *rises* faster than 1.0 per second (0.5 calm): climbing to the
cap takes about half a second, which keeps full flashes under three a second even for a strobing preset (tested against the WCAG 2.3.1 rule of thumb on the worst input at several speeds); getting darker is instant. Beats
fire at most 2.5 times a second. **Reduced motion:** the visuals start off, and if switched on run calm (motion x0.4, beats x0.4, 24 frames a second, stricter guard). The drawing size falls to as little as half on slow
devices, the engine stops when the tab is hidden, and a lost WebGL context is rebuilt. No WebGL means no visuals and a note. Settings are per person in the browser (`falsehorizon.v1.<username>`: on, intensity,
mode, auto, seconds, album art, favourites, last preset, your presets), validated on the way in. Nothing is
loaded from outside this site, and nothing from a preset is put on the page as HTML. **Importing MilkDrop presets** (phase 3). TOTR bundles no third-party presets; the "Import MilkDrop preset (.milk)" button (under "Look inside, change or keep a preset") takes files you already have, several at once (20 at most, 100 KB each), and says
plainly that they were made by other people for a different program and are used at your own risk. `falsehorizon-milk.js` (`FalseHorizonMilk.convert(text, fileName)`) reads the `key=value` lines and translates: the base numbers (decay, zoom, spin,
drift, stretch, warp, wave colour and size, echo, gamma), the init / per-frame / per-pixel formulas, up to 2 custom waveforms (with per-point formulas, dots, thick, spectrum) and 4 shapes (with instances), and MilkDrop's eight built-in wave
shapes mapped to our five. Marked `dialect: "milk"`, which makes the engine count y downwards and spin the other way as MilkDrop does, and scale the sound numbers up (x1.8; they were written for louder values). It **refuses** (with the reason) any
preset whose formulas use loops, memory arrays (`loop`, `while`, `megabuf`, `gmegabuf`, `exec2/3`, `memcpy`, `assign`) or bitwise `&` `|`, because the language has none and must always finish, and anything our parser rejects. It **ignores** the custom GPU shaders
(`warp_` / `comp_` lines), borders, motion vectors, textures on shapes and invert/brighten/darken, and every import carries a note saying it will not look exactly the same (and which parts were left out). The converted preset is an ordinary
preset: kept among yours, exported as a normal `.fh-preset.json`. The importer's test files were written for the tests in the real layout (nothing third-party is copied), so how a given community preset will look can only be known by trying it.

## Themes and the Settings page
`/settings` (tabs Account, Appearance, Site; `#appearance` etc.) replaces the old Account page. **Account**: profile (no email is stored: the `users` table has no such column), change password,
devices (this person's live sessions, sign out one or all). **Appearance**: six built-in themes (Dreamspace, ANSI 16 colors, Phosphor green, Amber monochrome with an iris logo mark, Paper (light), High contrast),
screen effects (scanlines, glow, drifting background), a theme editor (11 colors, name, logo), export/import of theme files. **Site** (needs the `settings` permission): the contact setting and a link to the Database page.

`static/theme.js` is loaded in `<head>` of every page by `render_page`, before `app.js`, so the look is there from the first frame. It sets the CSS variables `style.css` is built on
(`--bg --fg --pink ...` and the `--*-rgb` triplets for `rgba(var(--pink-rgb), .3)`), plus `data-logo / data-glow / data-scanlines / data-motion` on `<html>`. **Never write a color literal in CSS**: use a variable
(a test fails if one appears outside `:root`). Choices live in this browser's localStorage, per signed-in person (`totr.v1.<username>.*`, from `<meta name="totr-user">`); the sign-in page copies the last look applied on the device.
Nothing about themes reaches the server. Colors enter only as validated `#rrggbb` through `style.setProperty`; uploaded files are capped at 4 KB, need `"totr-theme": 1`, a name of at most 30 characters, a known logo and all 11 colors,
and are refused if the text, quiet text, warning or error colors cannot be read against the background (WCAG contrast 4.5 / 3 / 3). Up to 12 custom themes. The iris mark is an original drawing, not any company's logo.
Tests: `tests/theme_test.js` (Node, run from `test_settings.py`) and `tests/test_settings.py`.

## Adding a page
1. `templates/<name>.html` with `{{HEADER}}`, `{{FOOTER}}` and `<link rel="stylesheet" href="/static/style.css">`.
2. A route that calls `auth_web.render_page(request, "<name>.html", "<nav label>")`.
3. If it is admin-only, add its path prefix to `ADMIN_PREFIX` and a row to `PERMISSION_PREFIX` naming the permission it needs (and add the permission to `auth/permissions.py` if it is new), plus a link in `PAGE_NAV_ADMIN`, all in `auth/web.py`.
4. Test that anonymous and member users are refused and that the page loads with no CSP errors (see `testing.md`).

## Status tab
`modules.status` in `admin.html` asks `/api/health` (slow: checks every audio file) every 30 s and `/api/health/live` every 5 s, **only while the page is visible and the Status tab is open**; coming back to the page
refreshes at once. Everything from the server is shown with `textContent`. The live panels (listening now, data streamed, this computer, library and jobs, recent problems) hide with the tab.
Radio and Archive call `trackListening(audio, source, trackId)` from `app.js`, which sends the listening heartbeat on play, every 30 s while playing, on pause, and "stopped" when the page closes.

## Browser tests
`tests/test_radio.py` and `tests/test_library.py` drive the real pages in headless Edge/Chromium (`tests/browser_runner.py`): the page template becomes a stand-alone file, the server is replaced by a small stub
(`tests/browser/*_stubs.js`: canned answers, in-memory mixtapes, a generated test tone) and a driver script (`*_driver.js`) clicks through and reports. FalseHorizon (`tests/test_visuals.py`, `tests/browser/fh_smoke.html`) is tested in software WebGL
(SwiftShader) with a fake analyser, reading real pixels back after each frame (the browser clears the drawing buffer between frames). If no browser is found those parts print `skip` (set `EDGE_PATH` to point at one).

## Checking a page in a real browser
Headless Edge can load a page and capture console messages:
`msedge --headless --enable-logging=stderr --v=0 --virtual-time-budget=8000 --screenshot=out.png <url>`; grep the log for "Content Security Policy" or "Uncaught".
Headless mode freezes animation time, so judge motion in a normal browser.
