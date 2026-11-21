#!/usr/bin/env bash
# Install dibs and the dibsd user service.
#   curl -fsSL https://raw.githubusercontent.com/graydeon/dibs/main/install.sh | bash
set -euo pipefail

VERSION=${VERSION:-v0.1.0}
BASE=${BASE:-https://raw.githubusercontent.com/graydeon/dibs/$VERSION}

die() { echo "install.sh: $*" >&2; exit 1; }

command -v python3 >/dev/null || die "python3 not found; install it first (Debian/Ubuntu: sudo apt install python3)"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' ||
    die "python3 >= 3.11 required; found $(python3 -V 2>&1) (Debian/Ubuntu: sudo apt install python3)"
command -v curl >/dev/null || die "curl not found (Debian/Ubuntu: sudo apt install curl)"
command -v systemctl >/dev/null || die "systemctl not found; dibs needs systemd"
systemctl --user list-units --no-legend >/dev/null 2>&1 ||
    die "no systemd user manager reachable; log in to a user session (or enable lingering) first"

uid=$(id -u)
ctrl=/sys/fs/cgroup/user.slice/user-$uid.slice/user@$uid.service/cgroup.controllers
{ [ -f "$ctrl" ] && grep -qw cpu "$ctrl" && grep -qw memory "$ctrl"; } ||
    die "cgroup v2 cpu/memory controllers are not delegated to the user manager; dibs cannot cap jobs"
bus=${XDG_RUNTIME_DIR:-/run/user/$uid}/bus
[ -e "$bus" ] || die "no user D-Bus at $bus; install dbus-user-session (Debian/Ubuntu) and log in again"

mkdir -p "$HOME/.local/bin" "$HOME/.config/systemd/user"
self=${BASH_SOURCE[0]:-}
if [[ $self == */* ]] && [ -f "$(dirname "$self")/dibs" ]; then
    dir=$(dirname "$self")
    install -m 755 "$dir/dibs" "$HOME/.local/bin/dibs"
    install -m 644 "$dir/dibsd.service" "$HOME/.config/systemd/user/dibsd.service"
else
    curl -fsSL "$BASE/dibs" -o "$HOME/.local/bin/dibs"
    chmod 755 "$HOME/.local/bin/dibs"
    curl -fsSL "$BASE/dibsd.service" -o "$HOME/.config/systemd/user/dibsd.service"
fi

systemctl --user daemon-reload
if systemctl --user is-active --quiet dibsd; then
    systemctl --user restart dibsd
else
    systemctl --user enable --now dibsd
fi

for _ in $(seq 50); do "$HOME/.local/bin/dibs" ls >/dev/null 2>&1 && break; sleep 0.2; done
"$HOME/.local/bin/dibs" run --est 1m --cpus 1 --mem 256M -- echo ok

case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo "note: ~/.local/bin is not on PATH; add this line to ~/.profile:"
       echo '  export PATH="$HOME/.local/bin:$PATH"' ;;
esac
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != yes ] && [ -z "${DISPLAY:-}" ]; then
    echo "note: no graphical session; run 'loginctl enable-linger' so the daemon keeps running"
fi
echo "dibs installed; scheduler is running"
