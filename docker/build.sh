#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LANGUAGES=(go java python)
AGENTS=(claude codex opencode)

build_base() {
    local lang="$1"
    echo "==> building base image: vex-bench-${lang}-base"
    docker build \
        -t "vex-bench-${lang}-base:latest" \
        -f "${SCRIPT_DIR}/base.${lang}.Dockerfile" \
        "${SCRIPT_DIR}"
}

build_agent() {
    local lang="$1" agent="$2"
    local dockerfile="${SCRIPT_DIR}/${lang}.${agent}.Dockerfile"
    if [[ ! -f "$dockerfile" ]]; then
        echo "--- skipping ${lang}/${agent} (no Dockerfile)"
        return
    fi
    echo "==> building agent image: vex-bench-${lang}-${agent}"
    docker build \
        -t "vex-bench-${lang}-${agent}:latest" \
        -f "$dockerfile" \
        "${SCRIPT_DIR}"
}

if [[ $# -eq 0 ]]; then
    for lang in "${LANGUAGES[@]}"; do
        build_base "$lang"
        for agent in "${AGENTS[@]}"; do
            build_agent "$lang" "$agent"
        done
    done
elif [[ $# -eq 1 ]]; then
    build_base "$1"
    for agent in "${AGENTS[@]}"; do
        build_agent "$1" "$agent"
    done
elif [[ $# -eq 2 ]]; then
    build_base "$1"
    build_agent "$1" "$2"
else
    echo "Usage: $0 [language [agent]]"
    exit 1
fi
