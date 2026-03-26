#!/bin/bash
set -euo pipefail

HOST="127.0.0.1"
PORT="8765"
URL="http://${HOST}:${PORT}"
READY_TIMEOUT_SECONDS=20
SYNC_PROMPT="Dependencies are missing or stale. Run 'uv sync --all-extras' now? [y/N] "

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

print_manual_command() {
	printf "Manual launch command:\n  uv run dead-letter-ui --host %s --port %s\n" "$HOST" "$PORT"
}

print_repo_error() {
	printf "Launcher must stay inside the dead-letter repo root.\n"
	print_manual_command
}

require_repo_root() {
	local required_path
	for required_path in "pyproject.toml" "uv.lock"; do
		if [ ! -e "${repo_root}/${required_path}" ]; then
			print_repo_error
			exit 1
		fi
	done
}

probe_existing_server() {
	local response

	if ! response="$(curl -sS --max-time 2 "$URL" 2>/dev/null)"; then
		return 1
	fi

	if printf "%s" "$response" | grep -q "<title>dead-letter</title>"; then
		printf "dead-letter is already running at %s. Opening browser...\n" "$URL"
		open "$URL"
		exit 0
	fi

	printf "Port %s is occupied by another service. Stop it or launch dead-letter manually on a different port.\n" "$PORT"
	print_manual_command
	exit 1
}

require_uv() {
	if command -v uv >/dev/null 2>&1; then
		return 0
	fi

	printf "uv is not installed or not on PATH.\n"
	printf "Install uv, then run 'uv sync --all-extras' and try again.\n"
	exit 1
}

ensure_synced() {
	local answer

	if uv sync --check --all-extras >/dev/null 2>&1; then
		return 0
	fi

	printf "%s" "$SYNC_PROMPT"
	if ! read -r answer; then
		printf "\nNo response received. Exiting without launch.\n"
		exit 1
	fi

	case "$answer" in
	y | Y | yes | YES)
		if ! uv sync --all-extras; then
			printf "Dependency sync failed.\n"
			print_manual_command
			exit 1
		fi
		;;
	*)
		printf "Launch cancelled. Dependencies were not updated.\n"
		exit 0
		;;
	esac
}

wait_for_server() {
	local server_pid="$1"
	local remaining="$READY_TIMEOUT_SECONDS"

	while [ "$remaining" -gt 0 ]; do
		if ! kill -0 "$server_pid" 2>/dev/null; then
			local exit_code=0
			if wait "$server_pid"; then
				exit_code=0
			else
				exit_code="$?"
			fi
			printf "dead-letter-ui exited before the UI became ready.\n"
			print_manual_command
			exit "$exit_code"
		fi

		if curl -sS --max-time 2 "$URL" 2>/dev/null | grep -q "<title>dead-letter</title>"; then
			printf "Opening dead-letter at %s...\n" "$URL"
			open "$URL"
			return 0
		fi

		sleep 1
		remaining=$((remaining - 1))
	done

	printf "Timed out waiting for dead-letter-ui to become ready. Open %s manually if it comes up.\n" "$URL"
	print_manual_command
	return 0
}

forward_signal() {
	local signal="$1"
	if [ -n "${server_pid:-}" ] && kill -0 "$server_pid" 2>/dev/null; then
		kill "-${signal}" "$server_pid" 2>/dev/null || true
	fi
}

main() {
	require_repo_root
	cd "$repo_root"

	probe_existing_server || true
	require_uv
	ensure_synced

	printf "Starting dead-letter-ui at %s...\n" "$URL"
	uv run dead-letter-ui --host "$HOST" --port "$PORT" &
	server_pid="$!"

	trap 'forward_signal TERM' TERM
	trap 'forward_signal INT' INT

	wait_for_server "$server_pid"
	wait "$server_pid"
}

main "$@"
