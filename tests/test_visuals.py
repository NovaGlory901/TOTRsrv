"""FalseHorizon, the radio's visualizer: the Node unit tests of the equation language and the logic
(tests/falsehorizon_test.js), source checks (nothing loaded from outside, no eval, no inline HTML,
themed styles), and a run of the real engine in headless Edge (real WebGL, real pixels, every
built-in preset, a fade between presets, a fake audio analyser). The radio page's own controls are
clicked through in test_radio.py. Parts that need Node or Edge print "skip" and do not count as
passed when those programs are not installed."""

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
from check import check, finish, relay

sys.stdout.reconfigure(errors="replace")

FILES = [
    "falsehorizon-expr.js",
    "falsehorizon-presets.js",
    "falsehorizon-core.js",
    "falsehorizon-milk.js",
    "falsehorizon.js",
    "falsehorizon-ui.js",
]


def skip(name, why):
    print(f"skip {name}: {why}")


# Node unit tests
node = shutil.which("node")
if node:
    for script, label in (
        ("falsehorizon_test.js", "falsehorizon"),
        ("falsehorizon_milk_test.js", "milk import"),
    ):
        out = subprocess.run(
            [node, str(PF / "tests" / script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        lines = out.stdout.splitlines()
        relay(label, lines)
        check(
            f"{label}: the Node test file ran to the end (no crash, no failing line)",
            out.returncode == 0 and any(l.startswith("ok") for l in lines),
            out.stderr[-500:],
        )
    for js in FILES:
        r = subprocess.run(
            [node, "--check", str(PF / "static" / js)], capture_output=True, text=True
        )
        check(f"{js} is valid JavaScript", r.returncode == 0, r.stderr[:300])
else:
    skip("falsehorizon unit tests and syntax checks", "Node is not installed")

# source checks
src = {f: (PF / "static" / f).read_text(encoding="utf-8") for f in FILES}
code = {f: re.sub(r"^\s*//.*$", "", s, flags=re.M) for f, s in src.items()}
# the SVG namespace names drawn icons, it loads nothing
check(
    "falsehorizon: nothing is loaded from outside this site",
    not any(
        re.search(r"""['"]https?://""", c.replace("'http://www.w3.org/2000/svg'", ""))
        for c in code.values()
    ),
)
check(
    "falsehorizon: no eval, Function(), or string timers anywhere",
    not any(
        re.search(
            r"\beval\s*\(|new Function|\bFunction\s*\(|setTimeout\s*\(\s*['\"]|setInterval\s*\(\s*['\"]",
            c,
        )
        for c in code.values()
    ),
)
check(
    "falsehorizon: nothing is put on the page as HTML (text only, so a preset's name or text can never run)",
    not any(
        "innerHTML" in c or "insertAdjacentHTML" in c or "outerHTML" in c or "document.write" in c
        for c in code.values()
    ),
)
# the export line at the bottom names window; nothing else may
check(
    "falsehorizon: the formula language never touches the page or the network",
    not re.search(
        r"\b(document|window|fetch|XMLHttpRequest|localStorage|import\()",
        "\n".join(l for l in code["falsehorizon-expr.js"].splitlines() if "typeof window" not in l),
    ),
)
check(
    "falsehorizon: the core has no page access either (so the React app can use it as is)",
    not re.search(r"\b(document|fetch|XMLHttpRequest)\b", code["falsehorizon-core.js"]),
)
check(
    "falsehorizon: the engine has the flash guard, the calm mode, a smaller drawing size on slow devices, and pauses when the tab is hidden",
    all(
        x in code["falsehorizon.js"]
        for x in ("guard.feed", "setCalm", "scale", "visibilitychange", "1000 / 24")
    ),
)
check(
    "falsehorizon: the engine only needs WebGL 1 (works on older devices)",
    "'webgl'" in code["falsehorizon.js"] and "webgl2" not in code["falsehorizon.js"],
)
css = (PF / "static" / "falsehorizon.css").read_text(encoding="utf-8")
check(
    "falsehorizon.css: every colour is a theme variable",
    not re.findall(r"#[0-9a-fA-F]{3,8}\b|\brgba?\((?!var)[^)]*\d", css.replace("rgba(var(", "")),
    re.findall(r"#[0-9a-fA-F]{3,8}\b", css),
)
used = set(re.findall(r"var\((--[a-z0-9-]+)", css))
defined = set(
    re.findall(
        r"^\s*(--[a-z0-9-]+):", (PF / "static" / "style.css").read_text(encoding="utf-8"), re.M
    )
)
check(
    "falsehorizon.css: every variable it uses exists in the theme",
    used <= defined | {"--iris"},
    sorted(used - defined),
)

# the real engine in headless Edge
sys.path.insert(0, str(PF / "tests"))
import browser_runner as br

browser = br.find_browser()
if not browser:
    skip("FalseHorizon in a browser", "no Edge/Chromium found (set EDGE_PATH to run it)")
else:
    try:
        r = br.run(
            browser,
            PF / "tests" / "browser" / "fh_smoke.html",
            tmp,
            flags=br.WEBGL_FLAGS,
            budget=90000,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        r = None
        print("note: FalseHorizon run timed out")
    if r is None:
        check("FalseHorizon smoke test produced a result", False)
    elif not r.get("supported") and (r.get("reason") or "").startswith("This browser has no WebGL"):
        skip("FalseHorizon smoke test", "this browser has no WebGL here")
    else:
        pre = r.get("presets", [])
        check(
            "FalseHorizon: the shaders compile and link",
            r.get("supported") is True,
            r.get("reason"),
        )
        check("FalseHorizon: no script errors", not r.get("errors"), r.get("errors"))
        check(
            "FalseHorizon: all 17 built-in presets were shown, one after the other",
            [p["name"] for p in pre] == r.get("names")
            and len(pre) == 17
            and all(p["shown"] == p["name"] for p in pre),
        )
        check(
            "FalseHorizon: every preset drew without any WebGL error",
            all(p["glError"] == 0 for p in pre),
        )
        check(
            "FalseHorizon: every preset puts a real picture on screen (not black, not a flat field)",
            all(p["lit"] > 0.2 and p["mean"] > 0.02 for p in pre),
            [(p["name"], round(p["lit"], 2)) for p in pre if p["lit"] <= 0.2 or p["mean"] <= 0.02],
        )
        check(
            "FalseHorizon: no preset is a washed-out white wall (average brightness stays moderate, the guard caps the rest)",
            all(p["mean"] < 0.62 for p in pre),
            [(p["name"], round(p["mean"], 2)) for p in pre if p["mean"] >= 0.62],
        )
        check(
            "FalseHorizon: the presets look different from each other",
            len({p["hash"] for p in pre}) >= 16,
        )
        check(
            "FalseHorizon: changing preset blends (it is fading half a second in) and ends on the new one",
            r["fadeMid"]["fading"] is True
            and r["fadeEnd"]["fading"] is False
            and r["fadeEnd"]["shown"] == r["fadeMid"]["shown"],
            (r["fadeMid"]["fading"], r["fadeEnd"]),
        )
        check(
            "FalseHorizon: the picture stays up through the fade, in silence and in calm mode",
            r["fadeMid"]["sample"]["lit"] > 0.1
            and r["silent"]["lit"] > 0.1
            and r["calm"]["lit"] > 0.1,
            (r["fadeMid"]["sample"]["lit"], r["silent"]["lit"], r["calm"]["lit"]),
        )
        check(
            "FalseHorizon: the preset-changed call is made, with each new name",
            r["events"] == [r["names"][0], r["names"][5]],
            r["events"],
        )
        np = r["nextPrev"]
        check(
            "FalseHorizon: Next picks another preset and Previous comes back to the one before",
            np["a"]
            and np["b"]
            and np["a"] != np["b"]
            and np["back"] == np["a"]
            and np["a"] != r["lockedFrom"],
            np,
        )
        check(
            "FalseHorizon: the picture is drawn at a sensible size and with cover art loaded",
            r["inspect"]["width"] >= 160
            and r["inspect"]["hasCover"] is True
            and r["inspect"]["error"] == "",
            r["inspect"],
        )
        check(
            "FalseHorizon: stopping really stops the drawing",
            r.get("framesAfterStop") == 0 and r.get("running") is False,
            (r.get("framesAfterStop"), r.get("running")),
        )

shutil.rmtree(tmp, ignore_errors=True)
finish()
