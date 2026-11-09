"""Build the landing page (`demo/landing/` v4) as a plain static site for Vercel.

The `.dc.html` files are design-canvas pages: they load the canvas's `support.js`, hold head content in `<helmet>`,
fill `{{accent}}` from a canvas prop and link to each other by canvas file name. This turns each one into an ordinary
HTML page with the default accent, so a browser shows it without the canvas.

    python scripts/build_landing.py [--out dist/landing] [--deploy]

`--deploy` sends the build to Vercel project `tmrw-landing` as a production deployment, using `VERCEL_TOKEN` (in a
cloud session the network proxy supplies the token for api.vercel.com, so it can be left unset).
"""
import argparse
import json
import os
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANDING = ROOT / "demo" / "landing"

# canvas page -> served path (clean URLs: /patients serves patients.html)
PAGES = {
    "v4-clinics.dc.html": "index.html",
    "v4-clinics-zh.dc.html": "zh.html",
    "v4-patients.dc.html": "patients.html",
    "v4-patients-zh.dc.html": "patients-zh.html",
}
LINKS = {
    "Landing-v4-clinics.dc.html": "/",
    "Landing-v4-clinics-zh.dc.html": "/zh",
    "Landing-v4-patients.dc.html": "/patients",
    "Landing-v4-patients-zh.dc.html": "/patients-zh",
}


def accent_default(src):
    m = re.search(r"data-props='([^']*)'", src)
    return json.loads(m.group(1))["accent"]["default"]


def to_static(src):
    accent = accent_default(src)
    src = re.sub(r'<script src="\./support\.js"></script>\n?', "", src)
    src = re.sub(r'<script type="text/x-dc".*?</script>\n?', "", src, flags=re.S)
    helmet = re.search(r"<helmet>\n?(.*?)</helmet>\n?", src, re.S)
    src = src.replace(helmet.group(0), "")
    src = src.replace("</head>", helmet.group(1) + '<meta name="viewport" content="width=device-width, initial-scale=1">\n</head>')
    src = re.sub(r"<x-dc[^>]*>\n?", "", src).replace("</x-dc>\n", "").replace("</x-dc>", "")
    src = src.replace("{{accent}}", accent)
    for canvas, path in LINKS.items():
        src = src.replace(f'href="{canvas}"', f'href="{path}"')
    leftover = re.findall(r"\{\{[^}]*\}\}|\.dc\.html|support\.js|<helmet|<x-dc", src)
    if leftover:
        raise ValueError(f"canvas markup left after build: {sorted(set(leftover))}")
    return src


def build(out):
    out.mkdir(parents=True, exist_ok=True)
    for canvas, served in PAGES.items():
        (out / served).write_text(to_static((LANDING / canvas).read_text(encoding="utf-8")), encoding="utf-8")
    (out / "vercel.json").write_text(json.dumps({"cleanUrls": True}, indent=2) + "\n", encoding="utf-8")
    return out


def deploy(out, project="tmrw-landing"):
    files = [{"file": f.name, "data": f.read_text(encoding="utf-8")} for f in sorted(out.iterdir())]
    body = {"name": project, "target": "production", "files": files,
            "projectSettings": {"framework": None, "buildCommand": None, "outputDirectory": None}}
    req = urllib.request.Request(
        "https://api.vercel.com/v13/deployments?skipAutoDetectionConfirmation=1",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ.get('VERCEL_TOKEN', 'proxy-injected')}"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "dist" / "landing")
    ap.add_argument("--deploy", action="store_true")
    args = ap.parse_args()
    out = build(args.out)
    print(out)
    if args.deploy:
        d = deploy(out)
        print(d["id"], d.get("readyState"), "https://" + d["url"], *d.get("alias", []))
