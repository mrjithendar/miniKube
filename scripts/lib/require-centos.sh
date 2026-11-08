#!/usr/bin/env bash
# Historical filename/function retained for callers. Default policy is portable
# tooling; host-mutating callers explicitly request linux or centos operations.
roboshop_require_centos() {
  case "${1:-}" in --help|-h|help) return 0;; esac
  local operation="${2:-portable}" system arch
  system="$(uname -s)"; arch="$(uname -m)"
  case "$arch" in x86_64|aarch64|arm64) ;; *) echo 'ERROR: x86_64 or ARM64 required.' >&2; return 1;; esac
  if [[ "$system" == Darwin && "$operation" == portable ]]; then return 0; fi
  if [[ "$system" != Linux || ! -r /etc/os-release ]]; then
    echo "ERROR: $operation operation requires a supported Linux host." >&2; return 1
  fi
  local ID='' VERSION_ID='' NAME=''
  . /etc/os-release
  if [[ "$ID" == centos && "$NAME" == *Stream* && "$VERSION_ID" =~ ^(9|10)$ ]]; then return 0; fi
  if [[ "$operation" != centos ]]; then
    case "$ID:$VERSION_ID" in amzn:2023|ubuntu:22.04|ubuntu:24.04) return 0;; esac
  fi
  echo "ERROR: Unsupported host $ID $VERSION_ID for $operation operation. No changes made." >&2
  return 1
}
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then roboshop_require_centos "$@"; fi
