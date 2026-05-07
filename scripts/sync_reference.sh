#!/usr/bin/env bash
# Safe upstream sync helper for the Hermes fork workflow.
#
# This script:
#   1. fetches `origin` and `reference`
#   2. verifies the worktree is clean
#   3. creates a short-lived sync branch from `main`
#   4. merges `reference/main`
#   5. runs the canonical test runner if the merge succeeds
#
# It stops before merging back into `main` so you can review the result.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

REMOTE_FORK="${REMOTE_FORK:-origin}"
REMOTE_UPSTREAM="${REMOTE_UPSTREAM:-reference}"
BASE_BRANCH="${BASE_BRANCH:-main}"
DATE_STAMP="$(date +%F)"
SYNC_BRANCH_DEFAULT="sync/reference-${DATE_STAMP}"
SYNC_BRANCH="${SYNC_BRANCH:-$SYNC_BRANCH_DEFAULT}"
RUN_TESTS="${RUN_TESTS:-1}"

usage() {
  cat <<'EOF'
Usage:
  scripts/sync_reference.sh [--no-tests] [--branch <name>] [--yes]

Options:
  --no-tests       Skip scripts/run_tests.sh after a successful merge
  --branch NAME    Use a custom sync branch name
  --yes            Skip the confirmation prompt
  -h, --help       Show this help

Environment overrides:
  REMOTE_FORK      Defaults to origin
  REMOTE_UPSTREAM  Defaults to reference
  BASE_BRANCH      Defaults to main
  SYNC_BRANCH      Defaults to sync/reference-YYYY-MM-DD
  RUN_TESTS        Defaults to 1
EOF
}

die() {
  echo "error: $*" >&2
  exit 1
}

info() {
  echo "→ $*"
}

success() {
  echo "✓ $*"
}

confirm() {
  local prompt="$1"
  local reply
  read -r -p "$prompt [y/N] " reply
  case "$reply" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

ensure_repo_root() {
  cd "$REPO_ROOT"
}

ensure_branch_exists() {
  local branch="$1"
  git show-ref --verify --quiet "refs/heads/$branch" || die "missing local branch '$branch'"
}

ensure_remote_branch_exists() {
  local remote="$1"
  local branch="$2"
  git show-ref --verify --quiet "refs/remotes/$remote/$branch" || die "missing remote branch '$remote/$branch'"
}

ensure_clean_worktree() {
  local status
  status="$(git status --short)"
  if [ -n "$status" ]; then
    echo "$status" >&2
    die "worktree is not clean; commit or stash changes before syncing upstream"
  fi
}

print_conflict_help() {
  cat <<EOF

merge stopped due to conflicts on branch '$SYNC_BRANCH'

Next steps:
  1. Resolve conflicts
  2. Run: git add <resolved-files>
  3. Run: git commit
  4. Run: scripts/run_tests.sh
  5. Review the result
  6. Merge back with:
     git checkout $BASE_BRANCH
     git merge --no-ff $SYNC_BRANCH
EOF
}

AUTO_CONFIRM=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-tests)
      RUN_TESTS=0
      ;;
    --branch)
      shift
      [ "$#" -gt 0 ] || die "--branch requires a name"
      SYNC_BRANCH="$1"
      ;;
    --yes)
      AUTO_CONFIRM=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
  shift
done

ensure_repo_root

info "checking git remotes and local base branch"
git remote get-url "$REMOTE_FORK" >/dev/null 2>&1 || die "remote '$REMOTE_FORK' not found"
git remote get-url "$REMOTE_UPSTREAM" >/dev/null 2>&1 || die "remote '$REMOTE_UPSTREAM' not found"
ensure_branch_exists "$BASE_BRANCH"

info "verifying worktree is clean"
ensure_clean_worktree

info "fetching $REMOTE_FORK and $REMOTE_UPSTREAM"
git fetch --prune "$REMOTE_FORK"
git fetch --prune "$REMOTE_UPSTREAM"
ensure_remote_branch_exists "$REMOTE_UPSTREAM" "$BASE_BRANCH"

if git show-ref --verify --quiet "refs/heads/$SYNC_BRANCH"; then
  die "sync branch '$SYNC_BRANCH' already exists; delete it or choose --branch <name>"
fi

cat <<EOF

Upstream sync plan:
  fork remote:      $REMOTE_FORK
  upstream remote:  $REMOTE_UPSTREAM
  base branch:      $BASE_BRANCH
  sync branch:      $SYNC_BRANCH
  run tests:        $RUN_TESTS

This will create a new local branch from '$BASE_BRANCH',
merge '$REMOTE_UPSTREAM/$BASE_BRANCH' into it, and stop there for review.
EOF

if [ "$AUTO_CONFIRM" -ne 1 ]; then
  confirm "continue?" || die "cancelled"
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"

info "checking out $BASE_BRANCH"
git checkout "$BASE_BRANCH"

info "resetting local $BASE_BRANCH to $REMOTE_FORK/$BASE_BRANCH"
git merge --ff-only "$REMOTE_FORK/$BASE_BRANCH"

info "creating sync branch $SYNC_BRANCH"
git checkout -b "$SYNC_BRANCH"

info "merging $REMOTE_UPSTREAM/$BASE_BRANCH into $SYNC_BRANCH"
set +e
git merge --no-ff "$REMOTE_UPSTREAM/$BASE_BRANCH"
MERGE_EXIT=$?
set -e

if [ "$MERGE_EXIT" -ne 0 ]; then
  print_conflict_help
  exit "$MERGE_EXIT"
fi

success "merge completed on $SYNC_BRANCH"

if [ "$RUN_TESTS" = "1" ]; then
  info "running canonical test suite"
  "$REPO_ROOT/scripts/run_tests.sh"
  success "tests completed"
else
  info "skipping tests by request"
fi

cat <<EOF

Sync branch is ready: $SYNC_BRANCH

Suggested next steps:
  git log --oneline --decorate -n 10
  git diff $BASE_BRANCH...$SYNC_BRANCH --stat
  git checkout $BASE_BRANCH
  git merge --no-ff $SYNC_BRANCH
  git push $REMOTE_FORK $BASE_BRANCH

Previous branch before sync: $CURRENT_BRANCH
EOF
