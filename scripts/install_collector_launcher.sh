#!/usr/bin/env bash
set -euo pipefail

# Install a Linux .desktop launcher for the HP collector wrapper.
#
# Usage:
#   bash scripts/install_collector_launcher.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DESKTOP_DIR="${HOME}/.local/share/applications"
DESKTOP_FILE="${DESKTOP_DIR}/wifi-survey-collector.desktop"

mkdir -p "${DESKTOP_DIR}"

cat > "${DESKTOP_FILE}" <<EOF
[Desktop Entry]
Type=Application
Name=Wi-Fi Survey Collector
Comment=Launch the HeatMap HP Wi-Fi survey collector
Exec=/bin/bash -lc 'cd "${REPO_ROOT}" && python3 hp_collector/collector_launcher.py'
Path=${REPO_ROOT}
Terminal=false
Categories=Utility;Network;
EOF

echo "Installed: ${DESKTOP_FILE}"
echo "If it does not appear immediately, log out/in or run: update-desktop-database ${DESKTOP_DIR}"
