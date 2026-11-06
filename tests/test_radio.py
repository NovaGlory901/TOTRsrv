"""Radio front end: the Node unit tests of static/radio-core.js and DejaVuAmp's dejavuamp-core.js,
checks that the pages and scripts agree with each other, and a smoke test of the visualizer in
headless Edge (real WebGL, real pixels, a fake audio analyser). Parts that need Node or Edge print
"skip" and do not count as passed when those programs are not installed."""

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
os.chdir(PF)
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["COLLECTOR_DIR"] = str(tmp / "reports")
os.environ["MAINT_REPORT"] = str(tmp / "reports" / "maintenance.json")
from check import check, finish, relay

# a failure message may carry symbols the Windows console cannot print
sys.stdout.reconfigure(errors="replace")


def skip(name, why):
    print(f"skip {name}: {why}")


# Node unit tests of the radio's logic
node = shutil.which("node")
if node:
    for script in ("radio_core_test.js", "dejavuamp_core_test.js", "settings_carryover_test.js"):
        out = subprocess.run(
            [node, str(PF / "tests" / script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        lines = out.stdout.splitlines()
        relay(script.split("_test")[0].replace("_", " "), lines)
        check(
            "radio core: the Node test file ran to the end",
            any("ALL PASSED" in l or "FAILED" in l for l in lines),
            out.stderr[-500:],
        )
    for js in ("radio-core.js", "radio-viz.js", "dejavuamp-core.js", "dejavuamp.js"):
        r = subprocess.run(
            [node, "--check", str(PF / "static" / js)], capture_output=True, text=True
        )
        check(f"{js} is valid JavaScript", r.returncode == 0, r.stderr[:300])
    for page in ("radio", "archive", "users", "settings", "admin", "index"):
        html = (PF / "templates" / f"{page}.html").read_text(encoding="utf-8")
        js = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
        f = tmp / f"{page}.js"
        f.write_text(js, encoding="utf-8")
        r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
        check(
            f"{page}.html: the inline script is valid JavaScript", r.returncode == 0, r.stderr[:300]
        )
else:
    skip("radio core tests and JavaScript syntax checks", "Node is not installed")


# pages and scripts agree
def ids_used(js):
    return set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", js)) | set(
        re.findall(r"getElementById\('([A-Za-z0-9_-]+)'\)", js)
    )


def ids_defined(html):
    return set(re.findall(r'\bid="([A-Za-z0-9_-]+)"', html))


radio = (PF / "templates" / "radio.html").read_text(encoding="utf-8")
inline = "\n".join(re.findall(r"<script>(.*?)</script>", radio, re.S))
missing = sorted(ids_used(inline) - ids_defined(radio))
check("radio page: every element the script looks up exists", not missing, missing)
check(
    "radio page: the DejaVuAmp rack has its mount point and its stylesheet",
    'id="rack"' in radio and 'href="/static/dejavuamp.css"' in radio,
)
check(
    "radio page: the old 3-band controls are gone",
    not any(
        f'id="{x}"' in radio for x in ("eqBass", "eqMid", "eqTreble", "eqReset", "volFine", "speed")
    ),
)
check(
    "radio page: loads its scripts from this site, in order, before the page script",
    radio.index('src="/static/radio-core.js"')
    < radio.index('src="/static/radio-viz.js"')
    < radio.index('src="/static/falsehorizon-expr.js"')
    < radio.index('src="/static/falsehorizon-presets.js"')
    < radio.index('src="/static/falsehorizon-core.js"')
    < radio.index('src="/static/falsehorizon-milk.js"')
    < radio.index('src="/static/falsehorizon.js"')
    < radio.index('src="/static/falsehorizon-scene.js"')
    < radio.index('src="/static/falsehorizon-ui.js"')
    < radio.index('src="/static/dejavuamp-core.js"')
    < radio.index('src="/static/dejavuamp.js"')
    < radio.index('src="/static/settings-carryover.js"')
    < radio.index("<script>"),
)
check(
    "radio page: FalseHorizon has its mount point, both canvases (new and classic), its name label and stylesheet",
    all(
        x in radio
        for x in ('id="visuals"', 'id="viz2"', 'id="fhName"', 'href="/static/falsehorizon.css"')
    ),
)
check(
    "radio page: the old visuals controls are gone (FalseHorizon builds its own)",
    not any(f'id="{x}"' in radio for x in ("vizOn", "vizAmt", "vizAmtText", "audioNote")),
)
check(
    "radio page: no inline event handlers and no outside scripts",
    not re.search(r"\son[a-z]+=", radio) and not re.findall(r'<script[^>]+src="https?:', radio),
)
check(
    "radio page: has a previous button, a settings panel and a visuals toggle",
    all(x in radio for x in ('id="prev"', 'id="settings"', 'id="vizToggle"', 'id="viz"')),
)
check(
    "radio page: the visualizer canvas is hidden from screen readers and starts hidden",
    re.search(r'<canvas id="viz"[^>]*aria-hidden="true"[^>]*hidden', radio) is not None,
)
check(
    "radio page: honours reduced motion for the visuals",
    "prefers-reduced-motion" in radio and "setCalm" in inline and "reduce" in inline,
)
check(
    "radio page: the audio graph is built from a click (tune in), not at page load",
    re.search(r"ensureGraph\(\);\s*\$\('intro'\)\.hidden = true;", radio) is not None,
)
check(
    "radio page: visuals settings are remembered per person (loaded when we know who they are)",
    "ui.load(me)" in inline and "storage: store" in inline,
)
check(
    "radio page: previous and next use the song history",
    "history.back()" in inline and "history.forward()" in inline and "history.push(t)" in inline,
)
amp = (PF / "static" / "dejavuamp.js").read_text(encoding="utf-8")
ampcore = (PF / "static" / "dejavuamp-core.js").read_text(encoding="utf-8")
check(
    "DejaVuAmp: the EQ is low-shelf, peaking and high-shelf filters",
    all(x in ampcore for x in ("'lowshelf'", "'peaking'", "'highshelf'"))
    and "createBiquadFilter" in amp,
)
check(
    "DejaVuAmp: playback speed is set on the element",
    "playbackRate" in amp and "defaultPlaybackRate" in amp,
)
# the SVG namespace is a name for drawn icons, not something that is loaded
check(
    "DejaVuAmp: no innerHTML and nothing loaded from outside",
    "innerHTML" not in amp
    and "innerHTML" not in ampcore
    and not re.search(
        r"""['"]https?://""", (amp + ampcore).replace("'http://www.w3.org/2000/svg'", "")
    ),
)
check(
    "DejaVuAmp: the page hands it the audio element and drives it through three calls only",
    re.search(r"DejaVuAmp\.create\(\{\s*audio", inline) is not None
    and all(x in inline for x in ("onPrev", "onNext", "onToggle")),
)

for page in ("archive", "users", "settings"):
    html = (PF / "templates" / f"{page}.html").read_text(encoding="utf-8")
    inline_js = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    miss = sorted(ids_used(inline_js) - ids_defined(html))
    check(f"{page} page: every element the script looks up exists", not miss, miss)

viz = (PF / "static" / "radio-viz.js").read_text(encoding="utf-8")
check(
    "visualizer: loads nothing from outside this site (no http(s) URLs in the code)",
    not re.search(r"""['"]https?://""", viz),
)
check(
    "visualizer: has the datamosh, warp, sharpen, glitch and colour steps",
    all(x in viz for x in ("motion vector", "sin(", "sharp", "tear", "hue")),
)
check(
    "visualizer: effects are capped and a calm mode exists", "uCalm" in viz and "1000 / 24" in viz
)

# the scripts are served with the right type (nosniff would otherwise block them)
try:
    from fastapi.testclient import TestClient

    import main

    c = TestClient(main.app)
    for js in (
        "radio-core.js",
        "radio-viz.js",
        "dejavuamp-core.js",
        "dejavuamp.js",
        "falsehorizon-expr.js",
        "falsehorizon-presets.js",
        "falsehorizon-core.js",
        "falsehorizon-milk.js",
        "falsehorizon.js",
        "falsehorizon-scene.js",
        "falsehorizon-ui.js",
        "settings-carryover.js",
    ):
        r = c.get(f"/static/{js}")
        check(
            f"/static/{js} is served as JavaScript",
            r.status_code == 200 and "javascript" in r.headers["content-type"],
            r.headers.get("content-type"),
        )
    check(
        "radio page needs a sign-in",
        c.get("/radio", follow_redirects=False).status_code in (303, 503),
    )
except Exception as e:  # the app could not be imported on this machine
    check("static scripts are served", False, repr(e))

# the visualizer in a real browser
sys.path.insert(0, str(PF / "tests"))
import browser_runner as br

browser = br.find_browser()
if not browser:
    skip(
        "browser tests (visualizer and radio page)",
        "no Edge/Chromium found (set EDGE_PATH to run them)",
    )
else:
    try:
        result = br.run(
            browser,
            PF / "tests" / "browser" / "viz_smoke.html",
            tmp,
            flags=br.WEBGL_FLAGS,
            budget=12000,
        )
    except subprocess.TimeoutExpired:
        result = None
        print("note: visualizer run timed out")
    if result is None:
        check("visualizer smoke test produced a result", False)
    elif not result.get("supported") and (result.get("reason") or "").startswith(
        "This browser has no WebGL"
    ):
        skip("visualizer smoke test", "this browser has no WebGL here")
    else:
        samples = result.get("samples", [])
        normal = [s for s in samples if s["phase"] == "normal"]
        check(
            "visualizer: the shaders compile and link",
            result.get("supported") is True,
            result.get("reason"),
        )
        check("visualizer: no script errors", not result.get("errors"), result.get("errors"))
        check(
            "visualizer: frames were drawn without WebGL errors",
            len(samples) >= 20 and all(s["glError"] == 0 for s in samples),
            len(samples),
        )
        check(
            "visualizer: pictures appear (the frame is not black)",
            sum(s["lit"] > 0.3 for s in normal[3:]) >= 0.8 * max(1, len(normal[3:]))
            and len(normal) > 5,
            [round(s["lit"], 2) for s in normal][:6],
        )
        check(
            "visualizer: the picture moves from frame to frame",
            len({s["hash"] for s in normal}) >= 0.8 * len(normal) and len(normal) > 5,
        )
        check(
            "visualizer: it keeps running in calm mode and with no audio",
            any(s["phase"] == "calm" for s in samples)
            and any(s["phase"] == "silent" for s in samples),
        )
        check(
            "visualizer: stopping really stops the drawing",
            result.get("framesAfterStop") == 0 and result.get("running") is False,
            (result.get("framesAfterStop"), result.get("running")),
        )

# the real radio page, clicked through in a browser (server replaced by canned answers and a test
# tone)
if browser:
    page_file = br.page_from_template(
        tmp,
        "radio.html",
        "radio_stubs.js",
        "radio_driver.js",
        swaps=[
            ('src="/static/radio-core.js"', f'src="{br.uri("static", "radio-core.js")}"'),
            ('src="/static/radio-viz.js"', f'src="{br.uri("static", "radio-viz.js")}"'),
            (
                'src="/static/falsehorizon-expr.js"',
                f'src="{br.uri("static", "falsehorizon-expr.js")}"',
            ),
            (
                'src="/static/falsehorizon-presets.js"',
                f'src="{br.uri("static", "falsehorizon-presets.js")}"',
            ),
            (
                'src="/static/falsehorizon-core.js"',
                f'src="{br.uri("static", "falsehorizon-core.js")}"',
            ),
            (
                'src="/static/falsehorizon-milk.js"',
                f'src="{br.uri("static", "falsehorizon-milk.js")}"',
            ),
            ('src="/static/falsehorizon.js"', f'src="{br.uri("static", "falsehorizon.js")}"'),
            (
                'src="/static/falsehorizon-scene.js"',
                f'src="{br.uri("static", "falsehorizon-scene.js")}"',
            ),
            ('src="/static/falsehorizon-ui.js"', f'src="{br.uri("static", "falsehorizon-ui.js")}"'),
            ('href="/static/falsehorizon.css"', f'href="{br.uri("static", "falsehorizon.css")}"'),
            ('src="/static/dejavuamp-core.js"', f'src="{br.uri("static", "dejavuamp-core.js")}"'),
            ('src="/static/dejavuamp.js"', f'src="{br.uri("static", "dejavuamp.js")}"'),
            (
                'src="/static/settings-carryover.js"',
                f'src="{br.uri("static", "settings-carryover.js")}"',
            ),
            ('href="/static/dejavuamp.css"', f'href="{br.uri("static", "dejavuamp.css")}"'),
        ],
    )
    try:
        page_result = br.run(browser, page_file, tmp, flags=br.WEBGL_FLAGS, budget=20000)
    except subprocess.TimeoutExpired:
        page_result = None
        print("note: radio page run timed out")
    check("radio page in a browser: produced a result", page_result is not None)
    if page_result is not None:
        st = page_result["steps"]
        check(
            "radio page in a browser: no script errors anywhere",
            not page_result["errors"],
            page_result["errors"],
        )
        check(
            "radio page in a browser: starts off air, Prev hidden",
            st["beforeTune"] == {"introHidden": False, "title": "off air", "prevHidden": True},
        )
        check(
            "radio page in a browser: Tune in starts a song, shows Prev/Skip, builds the audio graph",
            st["afterTune"]["introHidden"]
            and st["afterTune"]["title"] == "Song 1"
            and not st["afterTune"]["prevHidden"]
            and not st["afterTune"]["skipHidden"]
            and st["afterTune"]["asked"] == "/api/tracks/1/audio"
            and st["afterTune"]["haveGraph"]
            and st["afterTune"]["ctxState"] == "running",
            st["afterTune"],
        )
        check(
            "radio page in a browser: playing tells the server which song and from where (the listening heartbeat)",
            {"track_id": 1, "state": "playing", "source": "radio"} in st["pingsAfterTune"],
            st["pingsAfterTune"],
        )
        check(
            "radio page in a browser: tuning out reports paused, and every ping is well-formed",
            st["pingsAtEnd"][-1]["state"] == "paused"
            and all(
                set(p) == {"track_id", "state", "source"} and p["source"] == "radio"
                for p in st["pingsAtEnd"]
            ),
            st["pingsAtEnd"][-2:],
        )
        check(
            "radio page in a browser: Skip, Skip, Prev, Prev, Prev goes 2, 3, 2, 1, and stays on 1 at the start",
            st["skipSkipPrevPrevPrev"] == ["Song 2", "Song 3", "Song 2", "Song 1", "Song 1"],
            st["skipSkipPrevPrevPrev"],
        )
        check(
            "radio page in a browser: Next after going back retraces without asking the server",
            st["nextRetraces"] == {"title": "Song 2", "askedServer": 0},
            st["nextRetraces"],
        )
        check(
            "radio page in a browser: Next past the end picks a new song (one request)",
            st["nextAtEndPicksNew"] == {"title": "Song 4", "askedServer": 1},
            st["nextAtEndPicksNew"],
        )
        check(
            "radio page in a browser: the gear opens the settings",
            st["settingsOpen"] == {"hidden": False, "expanded": "true"},
        )
        c = st["controls"]
        check(
            "radio page in a browser: the rack is built with a ticker, ten bands plus a preamp, and its presets",
            re.fullmatch(r"Artist \d - Song \d", st["rackShape"]["ticker"]) is not None
            and st["rackShape"]["bandInputs"] == 11
            and st["rackShape"]["presetOptions"] >= 19,
            st["rackShape"],
        )
        check(
            "radio page in a browser: the audio graph is a shelf, eight bells, a shelf, a panner and a 2048-point analyser",
            st["rackShape"]["graph"]
            and st["rackShape"]["types"] == ["lowshelf"] + ["peaking"] * 8 + ["highshelf"]
            and st["rackShape"]["panner"]
            and st["rackShape"]["fft"] == 2048,
            st["rackShape"],
        )
        check(
            "radio page in a browser: speed, band, volume, balance and mono controls reach the audio and show their values",
            c["rate"] == 1.5
            and c["defaultRate"] == 1.5
            and c["bands"][0] == 6
            and c["bands"][4] == -3.5
            and c["bands"][9] == 2
            and c["vol"] == "-19.5 dB"
            and c["rackVol"] == "-19.5 dB"
            and c["balText"] == "40% left"
            and c["pan"] == -0.4
            and c["mono"] is True
            and c["monoPressed"] == "true"
            and "mono" in c["info"]
            and "1.5x" in c["info"],
            c,
        )
        check(
            "radio page in a browser: moving the sliders by hand leaves the preset list on custom",
            c["preset"] == "",
            c["preset"],
        )
        check(
            "radio page in a browser: picking Rock sets the ten bands and the preamp",
            st["rock"]["bands"] == [5, 4, 3, 1, -1, -1.5, 0.5, 3, 4, 5]
            and st["rock"]["preampDb"] == -2
            and st["rock"]["shown"] == "Rock"
            and st["rock"]["firstBand"] == "5",
            st["rock"],
        )
        check(
            "radio page in a browser: the EQ switch zeroes the sound but keeps the sliders",
            st["eqOff"]["bands"] == [0] * 10
            and st["eqOff"]["preampDb"] == 0
            and st["eqOff"]["pressed"] == "false"
            and st["eqOff"]["sliderKept"] == "5",
            st["eqOff"],
        )
        check(
            "radio page in a browser: a curve can be saved under a name and is then selected",
            st["saveMine"]["yours"] == ["Mine"]
            and st["saveMine"]["shown"] == "Mine"
            and st["saveMine"]["deleteHidden"] is False,
            st["saveMine"],
        )
        check(
            "radio page in a browser: Reset makes the curve flat again",
            st["eqReset"] == {"bands": [0] * 10, "preampDb": 0, "shown": "Flat"},
            st["eqReset"],
        )
        d = st["display"]
        check(
            "radio page in a browser: the time display runs and the seek bar is live",
            re.fullmatch(r"\d\d:\d\d", d["time"]) is not None and d["seekEnabled"],
            d,
        )
        check(
            "radio page in a browser: the spectrum display draws something while the tone plays",
            d["litPixels"] > 0,
            d,
        )
        check(
            "radio page in a browser: the rack's Next button plays the next song and updates the ticker",
            st["rackNext"]["ticker"].endswith(" - " + st["rackNext"]["title"]),
            st["rackNext"],
        )
        check(
            "radio page in a browser: the rack's play/pause tunes out, then back in and the audio plays again",
            st["rackPause"] == {"toggleText": "Tune in", "button": "play"}
            and st["rackPlay"]["toggleText"] == "Tune out"
            and st["rackPlay"]["button"] == "pause"
            and st["rackPlay"]["playing"] is True,
            ascii((st["rackPause"], st["rackPlay"])),
        )
        if st["vizOn"]["supported"]:
            check(
                "radio page in a browser: the visuals toggle turns the visualizer off and on",
                st["vizOff"] == {"label": "Visuals: off", "hidden": True, "running": False}
                and st["vizOn"]["label"] == "Visuals: on"
                and st["vizOn"]["hidden"] is False
                and st["vizOn"]["running"] is True,
                (st["vizOff"], st["vizOn"]),
            )
            check(
                "radio page in a browser: the visuals take over the collage (it is drawn into them, not under them)",
                st["vizOn"]["scene"] is True and st["vizOn"]["stageClass"] is True,
                st["vizOn"],
            )
            check(
                "radio page in a browser: tuning out keeps the visuals moving, like MilkDrop on pause",
                st["tunedOut"] == {"toggleText": "Tune in", "vizHidden": False, "running": True},
                st["tunedOut"],
            )
        else:
            skip("radio page visuals toggle", "WebGL is not available in this browser")
        check("radio page in a browser: the intensity slider is labelled", st["amt"] == "40%")
        if st["vizOn"]["supported"]:
            fs = st["fhShape"]
            check(
                "radio page in a browser: FalseHorizon's controls list the built-in presets",
                fs["built"]
                and fs["presetOptions"] >= 17
                and "Built in" in fs["groups"]
                and fs["styleValue"] == "horizon",
                fs,
            )
            check(
                "radio page in a browser: picking a preset changes the picture, Next moves on and Previous comes back",
                st["fhPick"] == {"current": "Heartbeat", "shown": "Heartbeat"}
                and st["fhNextPrev"] == {"moved": True, "back": True},
                (st["fhPick"], st["fhNextPrev"]),
            )
            check(
                "radio page in a browser: Lock keeps the preset and Favourite adds a Favourites group",
                st["fhLock"] == {"locked": True, "pressed": "true"}
                and st["fhFav"]["pressed"] == "true"
                and "Favourites" in st["fhFav"]["groups"],
                (st["fhLock"], st["fhFav"]),
            )
            check(
                "radio page in a browser: the code box shows the preset as a preset file, and a broken formula is refused with a reason",
                st["fhCode"]["json"] == 1
                and st["fhBad"]["bad"] is True
                and "formula" in st["fhBad"]["say"],
                (st["fhCode"], st["fhBad"]),
            )
            check(
                "radio page in a browser: a preset you write can be tried and then saved under Yours",
                st["fhTry"]["current"] == "Mine Vis"
                and st["fhSave"]["current"] == "Mine Vis"
                and "Yours" in st["fhSave"]["groups"]
                and st["fhSave"]["deleteHidden"] is False,
                (st["fhTry"], st["fhSave"]),
            )
            check(
                "radio page in a browser: importing .milk files adds the good one (shown at once, under Yours), refuses the loop and the junk with reasons, and says the picture will differ",
                st["fhMilk"]["current"] == "Imported Sky"
                and "Yours" in st["fhMilk"]["groups"]
                and "Added 1: Imported Sky" in st["fhMilk"]["say"]
                and "will not look exactly" in st["fhMilk"]["say"]
                and "Loopy.milk" in st["fhMilk"]["say"]
                and "loop" in st["fhMilk"]["say"]
                and "Junk.milk" in st["fhMilk"]["say"]
                and st["fhMilk"]["sayBad"] is False,
                st["fhMilk"],
            )
            check(
                "radio page in a browser: importing only junk adds nothing, says so, and leaves the picture alone",
                st["fhMilkBad"]["sayBad"] is True
                and "Nothing was added" in st["fhMilkBad"]["say"]
                and st["fhMilkBad"]["current"] == "Imported Sky",
                st["fhMilkBad"],
            )
            check(
                "radio page in a browser: the Classic style swaps the picture over (and back) and hides the preset controls",
                st["fhClassic"]
                == {
                    "fhHidden": True,
                    "classicHidden": False,
                    "fhRunning": False,
                    "presetRowHidden": True,
                }
                and st["fhBack"] == {"fhHidden": False, "classicHidden": True, "fhRunning": True},
                (st["fhClassic"], st["fhBack"]),
            )
        saved = st["saved"]
        if isinstance(saved, dict):
            check(
                "radio page in a browser: audio settings are saved for this person (volume, speed, balance, mono, saved presets)",
                saved["volumeDb"] == -19.5
                and saved["speed"] == 1.5
                and saved["balance"] == -40
                and saved["mono"] is True
                and [p["name"] for p in saved["userPresets"]] == ["Mine"],
                saved,
            )
            check(
                "radio page in a browser: the visuals' settings are saved separately (intensity, on, your own presets, favourites)",
                st["savedViz"]["intensity"] == 40
                and st["savedViz"]["on"] is True
                and [p["name"] for p in st["savedViz"]["userPresets"]]
                == ["Mine Vis", "Imported Sky"]
                and "Heartbeat" in st["savedViz"]["favorites"],
                st["savedViz"],
            )
        else:
            skip("radio page saved settings", str(saved))


shutil.rmtree(tmp, ignore_errors=True)
finish()
