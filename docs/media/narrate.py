#!/usr/bin/env python3
"""Cut a narrated video from voice lines and footage. Needs ffmpeg and ffprobe.

    python3 docs/media/narrate.py edit.json

edit.json:
{
  "out": "docs/demo.mp4",
  "gap": 0.4,                        # silence after each line
  "font": "/path/to/font.ttf",       # optional; captions and cards
  "lines": [
    {"audio": "l01.mp3", "card": {"title": "pile", "sub": "Take back your inbox."}},
    {"audio": "l02.mp3", "video": "screen.mov", "start": 12.0, "end": 30.0,
     "crop": "x:y:w:h",              # optional, in source pixels, before scaling
     "blur": [[x, y, w, h], [x, y, w, h, t0, t1]],   # optional, source pixels; t relative to start
     "box":  [[x, y, w, h]],         # optional solid boxes, same form (safer than blur for text)
     "caption": "One feed"}
  ]
}

Each line's picture lasts as long as its audio plus the gap. Footage longer than that is
sped up to fit (up to "max_speed", default 2.5, then trimmed); shorter footage holds its
last frame. Output: 1280x720, 30 fps, H.264 + AAC, loudness-normalised to -14 LUFS.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

W, H, FPS = 1280, 720, 30


def dur(path):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "csv=p=0", path]).decode().strip())


def run(args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y"] + args, check=True)


def textfile(tmp, text):
    f = tempfile.NamedTemporaryFile("w", dir=tmp, suffix=".txt", delete=False)
    f.write(text)
    f.close()
    return f.name


def caption(tmp, font, text):
    return (",drawtext=fontfile=%s:textfile=%s:fontsize=30:fontcolor=white:box=1:boxcolor=black@0.72:"
            "boxborderw=16:x=(w-text_w)/2:y=h-86" % (font, textfile(tmp, text)))


def card(tmp, font, c, seconds, out):
    vf = ("drawtext=fontfile=%s:textfile=%s:fontsize=110:fontcolor=0xffb547:x=(w-text_w)/2:y=(h/2)-120"
          % (font, textfile(tmp, c.get("title", ""))))
    if c.get("sub"):
        vf += (",drawtext=fontfile=%s:textfile=%s:fontsize=40:fontcolor=0xe8e9ec:x=(w-text_w)/2:y=(h/2)+20"
               % (font, textfile(tmp, c["sub"])))
    if c.get("small"):
        vf += (",drawtext=fontfile=%s:textfile=%s:fontsize=26:fontcolor=0x9096a1:x=(w-text_w)/2:y=(h/2)+90"
               % (font, textfile(tmp, c["small"])))
    run(["-f", "lavfi", "-i", "color=c=0x0d0e11:s=%dx%d:r=%d:d=%.3f" % (W, H, FPS, seconds),
         "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", out])


def clip(tmp, font, line, seconds, out, max_speed):
    src = line["video"]
    start = float(line.get("start", 0))
    end = float(line.get("end", dur(src)))
    length = end - start
    speed = min(max(length / seconds, 1.0), max_speed)
    chain = []
    # Privacy first, in source pixels, so nothing readable survives scaling.
    regions = [("blur", r) for r in line.get("blur", [])] + [("box", r) for r in line.get("box", [])]
    graph = "[0:v]setpts=PTS-STARTPTS[v0]"
    last = "v0"
    for i, (kind, r) in enumerate(regions):
        x, y, w, h = r[:4]
        when = ":enable='between(t,%s,%s)'" % (r[4], r[5]) if len(r) >= 6 else ""
        if kind == "box":
            graph += ";[%s]drawbox=x=%d:y=%d:w=%d:h=%d:color=0x15171c:t=fill%s[v%d]" % (last, x, y, w, h, when, i + 1)
        else:
            graph += (";[%s]split[a%d][b%d];[b%d]crop=%d:%d:%d:%d,boxblur=20:3[c%d];[a%d][c%d]overlay=%d:%d%s[v%d]"
                      % (last, i, i, i, w, h, x, y, i, i, i, x, y, when, i + 1))
        last = "v%d" % (i + 1)
    post = []
    if line.get("crop"):
        post.append("crop=" + line["crop"])
    post += ["setpts=PTS/%.4f" % speed, "fps=%d" % FPS,
             "scale=%d:%d:force_original_aspect_ratio=decrease:flags=lanczos" % (W, H),
             "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=0x0d0e11" % (W, H),
             "tpad=stop_mode=clone:stop_duration=%.3f" % seconds, "trim=duration=%.3f" % seconds, "setsar=1"]
    vf = ",".join(post)
    if line.get("caption"):
        vf += caption(tmp, font, line["caption"])
    graph += ";[%s]%s[out]" % (last, vf)
    run(["-ss", "%.3f" % start, "-t", "%.3f" % length, "-i", src, "-filter_complex", graph, "-map", "[out]", "-an",
         "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-pix_fmt", "yuv420p", "-r", str(FPS), out])


def main(path):
    edit = json.load(open(path))
    base = os.path.dirname(os.path.abspath(path))
    font = edit.get("font") or subprocess.check_output(["fc-match", "-f", "%{file}", "sans:semibold"]).decode()
    gap = float(edit.get("gap", 0.4))
    tmp = tempfile.mkdtemp()
    parts, audio_inputs, filt = [], [], []
    for i, line in enumerate(edit["lines"]):
        for k in ("audio", "video"):
            if k in line and not os.path.isabs(line[k]):
                line[k] = os.path.join(base, line[k])
        seconds = dur(line["audio"]) + gap + float(line.get("hold", 0))
        out = os.path.join(tmp, "p%02d.mp4" % i)
        if "card" in line:
            card(tmp, font, line["card"], seconds, out)
        else:
            clip(tmp, font, line, seconds, out, float(edit.get("max_speed", 2.5)))
        parts.append(out)
        audio_inputs += ["-i", line["audio"]]
        filt.append("[%d:a]aresample=48000,aformat=channel_layouts=stereo,apad=whole_dur=%.3f[a%d]" % (i, seconds, i))
        print("line %d: %.2fs" % (i + 1, seconds), file=sys.stderr)
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w") as f:
        f.writelines("file '%s'\n" % p for p in parts)
    video = os.path.join(tmp, "video.mp4")
    run(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", video])
    n = len(edit["lines"])
    filt.append("".join("[a%d]" % i for i in range(n)) + "concat=n=%d:v=0:a=1[cat]" % n)
    raw = os.path.join(tmp, "voice.wav")
    run(audio_inputs + ["-filter_complex", ";".join(filt), "-map", "[cat]", raw])
    # Two-pass loudness normalisation to -14 LUFS.
    meas = subprocess.run(["ffmpeg", "-hide_banner", "-i", raw, "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
                           "-f", "null", "-"], capture_output=True, text=True).stderr
    m = json.loads(meas[meas.rindex("{"):meas.rindex("}") + 1])
    ln = ("loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=%s:measured_TP=%s:measured_LRA=%s:measured_thresh=%s:"
          "offset=%s:linear=true" % (m["input_i"], m["input_tp"], m["input_lra"], m["input_thresh"], m["target_offset"]))
    out = edit.get("out", "demo.mp4")
    if not os.path.isabs(out):
        out = os.path.join(base, out)
    run(["-i", video, "-i", raw, "-map", "0:v", "-map", "1:a", "-af", ln + ",aresample=48000", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", out])
    print(out, "%.1fs" % dur(out), "%.1f MB" % (os.path.getsize(out) / 1e6))


if __name__ == "__main__":
    main(sys.argv[1])
