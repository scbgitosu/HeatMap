#!/usr/bin/env bash
# Seamless launcher for the HP field collector GUI.
#
# Handles the two things that bite PyQt5 on Ubuntu:
#   1. Picking the right Qt platform plugin (wayland vs xcb) for the live session.
#   2. Telling you exactly which apt packages are missing if the plugin can't load.
#
# Usage:
#   ./scripts/run_collector.sh [--project survey_projects/apartment_test] [other collector_app.py args...]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

REQUIRED_APT_PKGS=(qtwayland5 libxcb-cursor0 libxkbcommon-x11-0 libxcb-xinerama0)
APT_HINT="sudo apt install network-manager iw ${REQUIRED_APT_PKGS[*]}"

log()  { printf '[run_collector] %s\n' "$*" >&2; }
warn() { printf '[run_collector] WARN: %s\n' "$*" >&2; }
fail() { printf '[run_collector] ERROR: %s\n' "$*" >&2; exit 1; }

if [[ -z "${VIRTUAL_ENV:-}" && -f "${REPO_ROOT}/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.venv/bin/activate"
    log "activated .venv"
fi

if ! command -v python >/dev/null 2>&1; then
    fail "python not found on PATH. Create the venv per README and re-run."
fi

detect_platform() {
    if [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
        local sock="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/${WAYLAND_DISPLAY}"
        if [[ -S "${sock}" ]]; then
            echo "wayland"
            return 0
        fi
    fi
    if [[ -n "${DISPLAY:-}" ]]; then
        echo "xcb"
        return 0
    fi
    echo ""
}

other_platform() {
    case "$1" in
        wayland) echo "xcb" ;;
        xcb)     echo "wayland" ;;
        *)       echo "" ;;
    esac
}

if command -v dpkg >/dev/null 2>&1; then
    missing=()
    for pkg in "${REQUIRED_APT_PKGS[@]}"; do
        if ! dpkg -s "${pkg}" >/dev/null 2>&1; then
            missing+=("${pkg}")
        fi
    done
    if (( ${#missing[@]} > 0 )); then
        warn "missing Qt platform support packages: ${missing[*]}"
        warn "install with: ${APT_HINT}"
    fi
fi

has_project=0
for arg in "$@"; do
    if [[ "${arg}" == "--project" || "${arg}" == --project=* ]]; then
        has_project=1
        break
    fi
done
if (( has_project == 0 )); then
    set -- --project survey_projects/apartment_test "$@"
    log "no --project supplied; defaulting to survey_projects/apartment_test"
fi

project_dir=""
next_is_project=0
for arg in "$@"; do
    if (( next_is_project )); then
        project_dir="${arg}"
        next_is_project=0
        continue
    fi
    case "${arg}" in
        --project=*) project_dir="${arg#--project=}" ;;
        --project) next_is_project=1 ;;
    esac
done
if [[ -z "${project_dir}" ]]; then
    project_dir="survey_projects/apartment_test"
fi

log "running Wi-Fi preflight for ${project_dir}..."
set +e
python hp_collector/preflight.py --project "${project_dir}"
preflight_status=$?
set -e
if (( preflight_status != 0 )); then
    fail "Wi-Fi preflight failed (see above). Fix the issues, then re-run."
fi

primary="$(detect_platform || true)"
if [[ -z "${primary}" ]]; then
    fail "no graphical session detected (WAYLAND_DISPLAY and DISPLAY both empty).
       Run this from the HP's desktop session, not over plain SSH.
       For SSH, use 'ssh -X' and ensure xauth is installed."
fi

session_type="${XDG_SESSION_TYPE:-unknown}"
log "session_type=${session_type} primary plugin=${primary}"

run_with() {
    local plat="$1"
    shift
    log "launching collector with QT_QPA_PLATFORM=${plat}"
    QT_QPA_PLATFORM="${plat}" python hp_collector/collector_app.py "$@"
}

set +e
run_with "${primary}" "$@"
status=$?
set -e

if (( status != 0 )); then
    fallback="$(other_platform "${primary}")"
    if [[ -n "${fallback}" ]]; then
        warn "primary plugin '${primary}' failed (exit ${status}); retrying with '${fallback}'"
        set +e
        run_with "${fallback}" "$@"
        status=$?
        set -e
    fi
fi

if (( status != 0 )); then
    cat >&2 <<EOF
[run_collector] ERROR: collector exited with status ${status}.

If Qt complained about a platform plugin (xcb / wayland / wl_display), install
the system packages:

    ${APT_HINT}

Then re-run this script. If the error persists, run with verbose Qt logging:

    QT_DEBUG_PLUGINS=1 ./scripts/run_collector.sh $*
EOF
    exit "${status}"
fi
