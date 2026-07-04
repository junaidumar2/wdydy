#!/usr/bin/env python3
"""
soundbite.py — build and maintain the soundboard.

Everything the board plays lives in sounds/ and is listed in soundbites.json.
This tool keeps both in sync, and gets audio out of podcasts for you.

Typical session:

    # 1. See what's in a podcast feed
    python3 tools/soundbite.py episodes https://feeds.acast.com/public/shows/6a2be260126ad95c2367834c

    # 2. Download an episode (goes to episodes/, which is git-ignored)
    python3 tools/soundbite.py fetch https://feeds.acast.com/public/shows/6a2be260126ad95c2367834c --episode 1

    # 3. Find the laugh in any player, note the time, cut it
    python3 tools/soundbite.py clip episodes/001-*.mp3 --start 23:41.5 --end 23:44 --id dod-laugh --label "DOD laugh" --emoji 😂

    # 4. Check the board locally, then push it live
    python3 -m http.server -d . 8000        # open http://localhost:8000
    python3 tools/soundbite.py publish

Requires: python3 (stdlib only) and ffmpeg on PATH.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_BOARD = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (personal-soundboard-tool)"}
ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


# ---------------------------------------------------------------- helpers --

def die(msg: str, code: int = 1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def require_ffmpeg():
    if not shutil.which("ffmpeg"):
        die("ffmpeg not found on PATH. Install it first, e.g.  sudo apt install ffmpeg")


def pick_codec():
    """Prefer mp3; fall back to aac/m4a if this ffmpeg build lacks libmp3lame."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                         capture_output=True, text=True).stdout
    if "libmp3lame" in out:
        return ["-c:a", "libmp3lame", "-q:a", "2"], ".mp3"
    return ["-c:a", "aac", "-b:a", "160k"], ".m4a"


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "sound"


def parse_time(t: str) -> float:
    """Accepts 83, 83.5, 1:23, 1:23.5, 1:02:03, 1:02:03.250 -> seconds."""
    parts = str(t).strip().split(":")
    if not 1 <= len(parts) <= 3:
        die(f"can't parse time '{t}' (use SS, MM:SS or HH:MM:SS, decimals ok)")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        die(f"can't parse time '{t}' (use SS, MM:SS or HH:MM:SS, decimals ok)")
    sec = 0.0
    for n in nums:
        sec = sec * 60 + n
    return sec


def fmt_secs(sec: float) -> str:
    sec = max(sec, 0)
    m, s = divmod(sec, 60)
    h, m = divmod(int(m), 60)
    return (f"{h}:{m:02d}:{s:04.1f}" if h else f"{int(m)}:{s:04.1f}")


def probe_duration(path: Path):
    if not shutil.which("ffprobe"):
        return None
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


# -------------------------------------------------------------- manifest --

def manifest_path(board: Path) -> Path:
    return board / "soundbites.json"


def load_manifest(board: Path) -> dict:
    p = manifest_path(board)
    if not p.exists():
        return {"title": "My Soundboard", "version": 0, "sounds": []}
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"{p} is not valid JSON ({e}). Fix or delete it and re-run.")
    m.setdefault("title", "My Soundboard")
    m.setdefault("version", 0)
    m.setdefault("sounds", [])
    return m


def save_manifest(board: Path, m: dict):
    m["version"] = int(m.get("version", 0)) + 1
    manifest_path(board).write_text(
        json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def upsert_sound(board: Path, entry: dict):
    m = load_manifest(board)
    existed = False
    for i, s in enumerate(m["sounds"]):
        if s.get("id") == entry["id"]:
            m["sounds"][i] = {**s, **entry}
            existed = True
            break
    if not existed:
        m["sounds"].append(entry)
    save_manifest(board, m)
    return existed


# ------------------------------------------------------------------- rss --

def fetch_url(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def rss_episodes(feed_url: str):
    """Returns newest-first list of dicts: title, date, duration, audio_url."""
    try:
        root = ET.fromstring(fetch_url(feed_url))
    except ET.ParseError as e:
        die(f"couldn't parse RSS feed ({e}) — is that definitely the feed URL?")
    except Exception as e:
        die(f"couldn't fetch feed: {e}")
    eps = []
    for item in root.iter("item"):
        enc = item.find("enclosure")
        title = item.findtext("title", "").strip() or "(untitled)"
        date = (item.findtext("pubDate", "") or "")[:16].strip()
        dur = (item.findtext(f"{ITUNES}duration", "") or "").strip()
        url = enc.get("url") if enc is not None else None
        if url:
            eps.append({"title": title, "date": date, "duration": dur, "audio_url": url})
    if not eps:
        die("feed parsed but contained no audio enclosures.")
    return eps


def cmd_episodes(a):
    eps = rss_episodes(a.feed)
    shown = 0
    for i, ep in enumerate(eps, 1):
        if a.search and a.search.lower() not in ep["title"].lower():
            continue
        dur = f"  [{ep['duration']}]" if ep["duration"] else ""
        print(f"{i:>4}  {ep['date']:<16}  {ep['title']}{dur}")
        if a.urls:
            print(f"      {ep['audio_url']}")
        shown += 1
        if shown >= a.limit:
            break
    if shown == 0:
        print("no episodes matched.")
    else:
        print(f"\n(1 = newest. Grab one with:  soundbite.py fetch {a.feed} --episode N)")


def cmd_fetch(a):
    board = Path(a.board).resolve()
    if a.episode:
        eps = rss_episodes(a.source)
        if not 1 <= a.episode <= len(eps):
            die(f"--episode must be 1..{len(eps)} for this feed")
        ep = eps[a.episode - 1]
        url, label = ep["audio_url"], ep["title"]
        print(f"Fetching #{a.episode}: {label}")
    else:
        url, label = a.source, a.source.rsplit("/", 1)[-1].split("?")[0]

    ext = ".mp3"
    tail = url.split("?")[0].lower()
    for e in (".m4a", ".mp3", ".aac", ".ogg", ".wav"):
        if tail.endswith(e):
            ext = e
            break

    dest_dir = board / "episodes"
    dest_dir.mkdir(exist_ok=True)
    stem = f"{a.episode:03d}-" if a.episode else ""
    dest = Path(a.out) if a.out else dest_dir / f"{stem}{slugify(label)[:60]}{ext}"

    req = urllib.request.Request(url, headers=UA)
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done, last = 0, -1
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    if pct // 5 != last:
                        last = pct // 5
                        print(f"\r  {pct:3d}%  ({done // 1048576} MB)", end="", flush=True)
        part.rename(dest)
    except Exception as e:
        part.unlink(missing_ok=True)
        die(f"download failed: {e}")
    print(f"\nSaved -> {dest}")
    print(f"Next:  python3 tools/soundbite.py clip \"{dest}\" --start MM:SS --end MM:SS --id my-clip")


# ------------------------------------------------------------------ clip --

def run_ffmpeg_clip(src: Path, out: Path, start: float, dur: float,
                    fade_ms: int, normalize: bool, gain_db: float, codec_args):
    fade = max(fade_ms, 0) / 1000.0
    filters = []
    if fade > 0:
        filters.append(f"afade=t=in:st=0:d={fade:.3f}")
        filters.append(f"afade=t=out:st={max(dur - fade, 0):.3f}:d={fade:.3f}")
    if normalize:
        filters.append("loudnorm=I=-14:TP=-1.5:LRA=11")
    if gain_db:
        filters.append(f"volume={gain_db}dB")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-vn"]
    if filters:
        cmd += ["-af", ",".join(filters)]
    cmd += codec_args + ["-ar", "44100", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        die(f"ffmpeg failed:\n{r.stderr.strip()}")


def cmd_clip(a):
    require_ffmpeg()
    board = Path(a.board).resolve()
    src = Path(a.input)
    if not src.exists():
        die(f"input file not found: {src}")

    start = parse_time(a.start)
    if a.end:
        end = parse_time(a.end)
        if end <= start:
            die("--end must be after --start")
        dur = end - start
    elif a.length:
        dur = float(a.length)
    else:
        die("give either --end or --length")
    if dur > 30:
        print(f"note: that's a {fmt_secs(dur)} clip — soundbites usually land under ~10s.")

    sound_id = slugify(a.id)
    codec_args, ext = pick_codec()
    sounds_dir = board / "sounds"
    sounds_dir.mkdir(exist_ok=True)
    out = sounds_dir / f"{sound_id}{ext}"

    run_ffmpeg_clip(src, out, start, dur, a.fade, not a.no_normalize, a.gain, codec_args)

    entry = {
        "id": sound_id,
        "label": a.label or sound_id.replace("-", " "),
        "emoji": a.emoji,
        "file": f"sounds/{out.name}",
    }
    replaced = upsert_sound(board, entry)
    kb = out.stat().st_size // 1024
    print(f"{'Replaced' if replaced else 'Added'} cart  {entry['emoji']}  \"{entry['label']}\""
          f"  ({fmt_secs(dur)}, {kb} KB) -> {out.relative_to(board)}")
    print("Preview:  python3 -m http.server -d . 8000   then open http://localhost:8000")


def cmd_add(a):
    require_ffmpeg()
    board = Path(a.board).resolve()
    src = Path(a.input)
    if not src.exists():
        die(f"input file not found: {src}")
    sound_id = slugify(a.id or src.stem)
    sounds_dir = board / "sounds"
    sounds_dir.mkdir(exist_ok=True)

    if a.copy and src.suffix.lower() in (".mp3", ".m4a", ".aac", ".wav", ".ogg"):
        out = sounds_dir / f"{sound_id}{src.suffix.lower()}"
        shutil.copy2(src, out)
    else:
        codec_args, ext = pick_codec()
        out = sounds_dir / f"{sound_id}{ext}"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(src), "-vn"] + codec_args + ["-ar", "44100", str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            die(f"ffmpeg failed:\n{r.stderr.strip()}")

    entry = {"id": sound_id, "label": a.label or sound_id.replace("-", " "),
             "emoji": a.emoji, "file": f"sounds/{out.name}"}
    replaced = upsert_sound(board, entry)
    print(f"{'Replaced' if replaced else 'Added'} cart  {entry['emoji']}  \"{entry['label']}\" -> {out.relative_to(board)}")


# ------------------------------------------------------------ housekeeping --

def cmd_list(a):
    board = Path(a.board).resolve()
    m = load_manifest(board)
    if not m["sounds"]:
        print("Board is empty. Cut something with the clip command.")
        return
    print(f"{m['title']}  (v{m['version']}, {len(m['sounds'])} carts)\n")
    for s in m["sounds"]:
        f = board / s.get("file", "")
        size = f"{f.stat().st_size // 1024:>4} KB" if f.exists() else " MISSING"
        d = probe_duration(f) if f.exists() else None
        dtxt = f" {fmt_secs(d)}" if d else ""
        print(f"  {s.get('emoji','🔊')}  {s['id']:<24} {s.get('label',''):<28}{size}{dtxt}")


def cmd_set(a):
    board = Path(a.board).resolve()
    m = load_manifest(board)
    for s in m["sounds"]:
        if s["id"] == a.id:
            if a.label:
                s["label"] = a.label
            if a.emoji:
                s["emoji"] = a.emoji
            save_manifest(board, m)
            print(f"Updated {a.id}: {s.get('emoji','')} \"{s.get('label','')}\"")
            return
    die(f"no cart with id '{a.id}' (see: soundbite.py list)")


def cmd_remove(a):
    board = Path(a.board).resolve()
    m = load_manifest(board)
    keep, gone = [], None
    for s in m["sounds"]:
        (keep.append(s) if s["id"] != a.id else None)
        if s["id"] == a.id:
            gone = s
    if gone is None:
        die(f"no cart with id '{a.id}' (see: soundbite.py list)")
    m["sounds"] = keep
    save_manifest(board, m)
    msg = f"Removed cart '{a.id}' from the board"
    if a.delete_file and gone.get("file"):
        f = board / gone["file"]
        if f.exists():
            f.unlink()
            msg += " and deleted its audio file"
    else:
        msg += " (audio file kept; add --delete-file to remove it too)"
    print(msg + ".")


def cmd_publish(a):
    board = Path(a.board).resolve()
    if not (board / ".git").exists():
        die("this folder isn't a git repo yet — see 'Putting it on your phone' in README.md")
    steps = [
        ["git", "add", "soundbites.json", "sounds", "index.html", "sw.js",
         "manifest.webmanifest", "icon-180.png", "icon-512.png", ".gitignore"],
        ["git", "commit", "-m", a.message],
        ["git", "push"],
    ]
    for cmd in steps:
        r = subprocess.run(cmd, cwd=board, capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            if "nothing to commit" in out:
                print("Nothing new to publish.")
                return
            die(f"'{' '.join(cmd[:2])}' failed:\n{out}")
    print("Pushed. GitHub Pages usually updates within a minute — hard-refresh the board.")


# ------------------------------------------------------------------ main --

def main():
    p = argparse.ArgumentParser(
        prog="soundbite.py",
        description="Cut soundbites from podcasts and maintain the soundboard.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Typical session:")[1])
    p.add_argument("--board", default=str(DEFAULT_BOARD),
                   help="soundboard folder (default: parent of this script)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("episodes", help="list episodes in a podcast RSS feed")
    s.add_argument("feed", help="RSS feed URL")
    s.add_argument("--limit", type=int, default=15)
    s.add_argument("--search", help="only show titles containing this text")
    s.add_argument("--urls", action="store_true", help="also print audio URLs")
    s.set_defaults(func=cmd_episodes)

    s = sub.add_parser("fetch", help="download an episode (to episodes/)")
    s.add_argument("source", help="RSS feed URL (with --episode) or a direct audio URL")
    s.add_argument("--episode", type=int, help="episode number from 'episodes' (1 = newest)")
    s.add_argument("-o", "--out", help="explicit output path")
    s.set_defaults(func=cmd_fetch)

    s = sub.add_parser("clip", help="cut a soundbite out of an audio file")
    s.add_argument("input", help="source audio (e.g. episodes/001-....mp3)")
    s.add_argument("--start", required=True, help="e.g. 23:41.5 or 1:02:03")
    s.add_argument("--end", help="end time (same formats)")
    s.add_argument("--length", type=float, help="...or clip length in seconds")
    s.add_argument("--id", required=True, help="short slug, becomes the filename")
    s.add_argument("--label", help="button text (default: id with spaces)")
    s.add_argument("--emoji", default="🔊", help="button emoji (default 🔊)")
    s.add_argument("--fade", type=int, default=25, help="fade in/out in ms (default 25)")
    s.add_argument("--gain", type=float, default=0, help="extra gain in dB")
    s.add_argument("--no-normalize", action="store_true", help="skip loudness normalization")
    s.set_defaults(func=cmd_clip)

    s = sub.add_parser("add", help="add an existing audio file as a cart")
    s.add_argument("input")
    s.add_argument("--id", help="slug (default: from filename)")
    s.add_argument("--label")
    s.add_argument("--emoji", default="🔊")
    s.add_argument("--copy", action="store_true", help="copy as-is instead of re-encoding")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("list", help="show every cart on the board")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("set", help="rename a cart's label/emoji")
    s.add_argument("id")
    s.add_argument("--label")
    s.add_argument("--emoji")
    s.set_defaults(func=cmd_set)

    s = sub.add_parser("remove", help="take a cart off the board")
    s.add_argument("id")
    s.add_argument("--delete-file", action="store_true", help="also delete the audio file")
    s.set_defaults(func=cmd_remove)

    s = sub.add_parser("publish", help="git add + commit + push the board")
    s.add_argument("-m", "--message", default="Update soundboard")
    s.set_defaults(func=cmd_publish)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
