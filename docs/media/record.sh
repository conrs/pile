#!/usr/bin/env bash
# Records docs/setup.mp4, docs/hero.gif and docs/learning.gif from scratch.
# Needs Linux with Xvfb, Firefox, xdotool, ImageMagick and ffmpeg. Everything runs on demo data.
set -euo pipefail
cd "$(dirname "$0")/../.."
REPO=$PWD
WORK=$(mktemp -d "${TMPDIR:-/tmp}/pile-rec.XXXX")
D=:98
PORT=${PORT:-8791}
FONT=${FONT:-$(fc-match -f '%{file}' 'Adwaita Sans:semibold')}
export PILE_NO_OPEN=1 XDG_DATA_HOME=$WORK/data XDG_CONFIG_HOME=$WORK/config GH_CONFIG_DIR=$WORK/gh
unset GITHUB_TOKEN GH_TOKEN PILE_IMAP_USER PILE_BASE_URL BASE_URL || true

cleanup() { kill $(jobs -p) 2>/dev/null || true; rm -rf "$WORK"; }
trap cleanup EXIT

Xvfb $D -screen 0 1280x720x24 >/dev/null 2>&1 &
sleep 1
python3 -m pile serve --demo --port $PORT >"$WORK/serve.log" 2>&1 &
mkdir -p "$WORK/ff"
cat >"$WORK/ff/user.js" <<'EOF'
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("ui.systemUsesDarkTheme", 1);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("layout.css.devPixelsPerPx", "1.3");
EOF
export DISPLAY=$D
firefox --no-remote --profile "$WORK/ff" --kiosk about:blank >/dev/null 2>&1 &
sleep 6
for w in $(xdotool search --class firefox); do xdotool windowsize "$w" 1280 720 windowmove "$w" 0 0 2>/dev/null || true; done
xdotool mousemove 1279 719

go() { xdotool key ctrl+l; sleep 0.2; xdotool type --delay 2 "$1"; xdotool key Return; }
rec() { ffmpeg -loglevel error -y -f x11grab -video_size 1280x720 -framerate 25 -i $D -c:v libx264 -preset ultrafast -crf 18 "$1" & REC=$!; T0=$(date +%s.%N); }
stop() { kill -INT $REC; wait $REC 2>/dev/null || true; }
mark() { echo "$1 $(calc "$(date +%s.%N) - $T0")" >>"$WORK/marks"; }
k() { xdotool key "$@"; }
calc() { python3 -c "print(round($1, 2))"; }

# 1. setup in a terminal
go "file://$REPO/docs/media/terminal.html#setup"; sleep 1.5
go "file://$REPO/docs/media/terminal.html#setup"; sleep 0.3
rec "$WORK/1.mkv"; sleep 25; stop

# 2. the feed
go "http://127.0.0.1:$PORT/"; sleep 2
: >"$WORK/marks"
rec "$WORK/2.mkv"; sleep 1.5
mark feed
k j; sleep 0.6; k j; sleep 0.6; k k; sleep 0.6; k k; sleep 0.8
mark open
k o; sleep 1.6
mark fix
k n; sleep 0.8
xdotool type --delay 35 "https://acme.slack.com/archives/launch"; sleep 0.6
k Return; sleep 2.4
k question; sleep 2.6; k Escape; sleep 0.6
mark hero
k j; sleep 0.4; k j; sleep 0.4; k k; sleep 0.4
k o; sleep 0.9; k y; sleep 0.6
k e; sleep 0.7
k s; sleep 0.8; k t; sleep 1.0
for _ in $(seq 1 30); do k e; sleep 0.3; done
sleep 2.4
mark end
stop

# 3. any agent, over MCP
go "file://$REPO/docs/media/terminal.html#agent"; sleep 0.3
rec "$WORK/3.mkv"; sleep 21; stop

if [ -n "${KEEP_RAW:-}" ]; then  # uncaptioned footage, e.g. for narrate.py
  mkdir -p "$KEEP_RAW"; cp "$WORK"/1.mkv "$WORK"/2.mkv "$WORK"/3.mkv "$WORK/marks" "$KEEP_RAW"/
fi
m() { awk -v n="$1" '$1==n{print $2}' "$WORK/marks"; }
FEED=$(m feed); OPEN=$(m open); FIX=$(m fix); HERO=$(m hero); END=$(m end)

# Captions: one text file per caption, so nothing needs escaping.
cap() { # file start end text...
  local f="$WORK/cap$RANDOM.txt"; printf '%s' "$4" >"$f"
  echo -n ",drawtext=fontfile=$FONT:textfile=$f:fontsize=30:fontcolor=white:box=1:boxcolor=black@0.72:boxborderw=16:x=(w-text_w)/2:y=h-86:enable='between(t,$2,$3)'"
}
F1="null$(cap x 0.5 5 'Clone it. Python 3 only, nothing to install.')$(cap x 5.2 13.5 'doctor says what works on this machine and how to turn on the rest')$(cap x 13.8 25 'Demo data here. On your Mac: grant Full Disk Access and drop --demo.')"
F2="null$(cap x 0 "$OPEN" 'One feed: Slack, Discord, Messages, GitHub, Gmail, Linear')$(cap x "$OPEN" "$FIX" 'o opens the actual thread')$(cap x "$FIX" "$(calc "$HERO - 3.2")" 'Wrong place? Paste where you ended up. It learns.')$(cap x "$(calc "$HERO - 3.2")" "$HERO" 'What it has learned, and you can delete any of it')$(cap x "$HERO" "$(calc "$END - 2.6")" 'e done, s snooze')$(cap x "$(calc "$END - 2.6")" 99 'Inbox zero')"
F3="null$(cap x 0 5 'Any agent can work it over MCP')$(cap x 5 21 'It reads your inbox and clears it with you')"

enc() { ffmpeg -loglevel error -y -i "$1" -vf "$2" -c:v libx264 -preset slow -crf 30 -pix_fmt yuv420p -r 25 -an "$3"; }
enc "$WORK/1.mkv" "$F1" "$WORK/1.mp4"
enc "$WORK/2.mkv" "$F2" "$WORK/2.mp4"
enc "$WORK/3.mkv" "$F3" "$WORK/3.mp4"
printf "file '%s'\n" "$WORK/1.mp4" "$WORK/2.mp4" "$WORK/3.mp4" >"$WORK/list.txt"
ffmpeg -loglevel error -y -f concat -safe 0 -i "$WORK/list.txt" -c copy -movflags +faststart docs/setup.mp4

gif() { # in start duration out width speed
  ffmpeg -loglevel error -y -ss "$2" -t "$3" -i "$1" -vf "setpts=PTS/${6:-1},fps=12,scale=$5:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" "$4"
}
gif "$WORK/2.mkv" "$HERO" "$(calc "$END - $HERO")" docs/hero.gif 800 2
gif "$WORK/2.mkv" "$OPEN" "$(calc "$HERO - $OPEN - 0.4")" docs/learning.gif 720

ls -la docs/setup.mp4 docs/hero.gif docs/learning.gif
