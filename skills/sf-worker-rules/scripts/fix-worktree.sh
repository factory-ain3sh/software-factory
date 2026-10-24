#!/usr/bin/env bash
# Prepare the one factory-mono checkout a Software Factory worker run may change.
#
#   fix-worktree.sh new <branch> [--base <branch>]   worktree on <branch>, created from fresh origin/dev
#                                                     (or from origin/<base>, to stack a fix on another PR's branch)
#   fix-worktree.sh pr <number>                       detached worktree at the current head of PR <number>, as pr-<number>
#
# The ain3sh-dev template denies branch and history commands that touch the
# primary checkout (~/repos/factory-mono stays on dev), so every branch and
# every PR checkout gets its own worktree under ~/repos/factory-mono-worktrees/.
# Prints the worktree path on the last line. Exits non-zero, before any
# mutation, when the branch name or PR number breaks the rules (3), an
# existing worktree is dirty, on another branch, or not the detached PR
# checkout expected (4), a local branch and origin have diverged (4), or a
# remote lookup or fetch fails for a reason other than the ref being absent
# (1). A branch that already has a clean worktree is reused; a branch that
# already exists on origin (a resumed implementation) is continued from there;
# a clean pr-<number> worktree is re-pointed at the PR's current head. Nothing
# here ever resets an existing branch.
set -euo pipefail

mode="${1:-}"
repo="$HOME/repos/factory-mono"
root="$HOME/repos/factory-mono-worktrees"
base="dev"
branch=""
number=""

usage() {
  echo "usage: fix-worktree.sh new <branch> [--base <branch>] | fix-worktree.sh pr <number>" >&2
  exit 2
}

case "$mode" in
  new)
    branch="${2:-}"
    [[ -n "$branch" ]] || usage
    if [[ $# -eq 4 && "$3" == --base && -n "$4" ]]; then
      base="$4"
    elif [[ $# -ne 2 ]]; then
      usage
    fi
    ;;
  pr)
    number="${2:-}"
    [[ $# -eq 2 && -n "$number" ]] || usage
    ;;
  *) usage ;;
esac

# "present" or "absent" for refs/heads/<branch> on origin. Any other outcome
# (authentication, transport) is a failed lookup, not an absent ref.
remote_branch_state() {
  local branch="$1" rc=0
  git -C "$repo" ls-remote --exit-code --heads origin "refs/heads/$branch" >/dev/null 2>/tmp/fix-worktree-ls-remote.err || rc=$?
  case "$rc" in
    0) echo present ;;
    2) echo absent ;;
    *)
      echo "ls-remote origin $branch failed (exit $rc): $(cat /tmp/fix-worktree-ls-remote.err)" >&2
      exit 1
      ;;
  esac
}

# The OID of refs/pull/<number>/head on origin; an absent ref or a failed
# lookup is exit 1.
pin_pr_head() {
  local out rc=0
  out="$(git -C "$repo" ls-remote --exit-code origin "refs/pull/$number/head" 2>/tmp/fix-worktree-ls-remote.err)" || rc=$?
  case "$rc" in
    0) echo "${out%%[[:space:]]*}" ;;
    2)
      echo "refusing: PR $number has no head on origin (refs/pull/$number/head is absent)" >&2
      exit 1
      ;;
    *)
      echo "ls-remote origin refs/pull/$number/head failed (exit $rc): $(cat /tmp/fix-worktree-ls-remote.err)" >&2
      exit 1
      ;;
  esac
}

# Fetch one ref into the primary checkout; a failure is a failed lookup (1).
fetch_ref() {
  local refspec="$1" what="$2"
  if ! git -C "$repo" fetch --prune origin "$refspec" 2>/tmp/fix-worktree-fetch.err; then
    echo "fetching $what failed: $(cat /tmp/fix-worktree-fetch.err)" >&2
    exit 1
  fi
}

# Refuse a worktree with uncommitted or untracked changes.
check_clean() {
  local dir="$1"
  if [[ -n "$(git -C "$dir" status --porcelain --untracked-files=normal)" ]]; then
    echo "refusing: $dir has uncommitted changes:" >&2
    git -C "$dir" status --short >&2
    exit 4
  fi
}

# Refuse a worktree that is not a clean checkout of the expected branch.
check_existing() {
  local dir="$1" branch="$2" current
  current="$(git -C "$dir" symbolic-ref -q --short HEAD || true)"
  if [[ "$current" != "$branch" ]]; then
    echo "refusing: $dir is on '${current:-detached}', expected $branch" >&2
    exit 4
  fi
  check_clean "$dir"
}

# Refuse a pr-<number> worktree that is on a branch (not this script's detached
# checkout) or dirty.
check_existing_pr() {
  local dir="$1" current
  current="$(git -C "$dir" symbolic-ref -q --short HEAD || true)"
  if [[ -n "$current" ]]; then
    echo "refusing: $dir is on branch '$current', expected a detached PR checkout" >&2
    exit 4
  fi
  check_clean "$dir"
}

# Bring a clean worktree level with origin/<branch>: fast-forward when behind,
# keep local commits when ahead, refuse when the two have diverged.
reconcile() {
  local dir="$1" branch="$2" ahead behind
  ahead="$(git -C "$dir" rev-list --count "origin/$branch..HEAD")"
  behind="$(git -C "$dir" rev-list --count "HEAD..origin/$branch")"
  if [[ "$ahead" != 0 && "$behind" != 0 ]]; then
    echo "refusing: $branch has $ahead local commit(s) and origin/$branch has $behind other(s); the two diverged" >&2
    exit 4
  elif [[ "$ahead" != 0 ]]; then
    echo "note: $dir holds $ahead local commit(s) not on origin/$branch; keep them" >&2
  elif [[ "$behind" != 0 ]]; then
    git -C "$dir" merge --ff-only "origin/$branch"
  fi
}

local_branch_exists() {
  git -C "$repo" show-ref --verify --quiet "refs/heads/$1"
}

if [[ $mode == pr ]]; then
  if [[ ! "$number" =~ ^[1-9][0-9]*$ ]]; then
    echo "refusing: PR number '$number' must be a positive integer" >&2
    exit 3
  fi
  dir="$root/pr-$number"
  # Pin the PR head's OID from the remote, fetch its ref, and check out the
  # pinned OID itself. FETCH_HEAD is shared by every fetch in the primary
  # checkout, so reading it could hand back whatever another fetch wrote.
  head="$(pin_pr_head)"
  fetch_ref "pull/$number/head" "pull/$number/head"
  if ! git -C "$repo" cat-file -e "$head^{commit}" 2>/dev/null; then
    # The head moved between the pin and the fetch (a force-push); pin once more.
    head="$(pin_pr_head)"
    fetch_ref "pull/$number/head" "pull/$number/head"
    if ! git -C "$repo" cat-file -e "$head^{commit}" 2>/dev/null; then
      echo "refusing: pinned head $head of PR $number is not present after fetching pull/$number/head" >&2
      exit 1
    fi
  fi
  if [[ -e "$dir" ]]; then
    check_existing_pr "$dir"
    git -C "$dir" checkout --quiet --detach "$head"
    echo "re-pointed existing worktree" >&2
  else
    mkdir -p "$root"
    git -C "$repo" worktree add --quiet --detach "$dir" "$head"
  fi
  echo "pr=$number head=${head:0:12}"
  echo "$dir"
  exit 0
fi

# pr-conventions.yml: <= 32 characters, [A-Za-z0-9/-] only; Ainesh's
# branches live under ain3sh/.
if [[ ${#branch} -gt 32 || ! "$branch" =~ ^ain3sh/[A-Za-z0-9/-]+$ ]]; then
  echo "refusing: branch '$branch' must match ^ain3sh/[A-Za-z0-9/-]+$ and be at most 32 characters" >&2
  exit 3
fi
if [[ "$base" == "$branch" ]] || ! git check-ref-format --branch "$base" >/dev/null 2>&1; then
  echo "refusing: base '$base' is not a valid branch name distinct from $branch" >&2
  exit 3
fi
dir="$root/${branch//\//__}"
fetch_ref "+refs/heads/$base:refs/remotes/origin/$base" "origin/$base"
remote="$(remote_branch_state "$branch")"
if [[ $remote == present ]]; then
  git -C "$repo" fetch origin "+refs/heads/$branch:refs/remotes/origin/$branch"
fi
base_sha="$(git -C "$repo" rev-parse "origin/$base")"

if [[ -e "$dir" ]]; then
  check_existing "$dir" "$branch"
  [[ $remote == present ]] && reconcile "$dir" "$branch"
  echo "reused existing worktree" >&2
elif local_branch_exists "$branch"; then
  # The branch survived a removed worktree (an earlier run on this
  # Computer); check it out as it is rather than resetting it.
  mkdir -p "$root"
  git -C "$repo" worktree add --quiet "$dir" "$branch"
  [[ $remote == present ]] && reconcile "$dir" "$branch"
  echo "note: checked out existing local branch $branch" >&2
elif [[ $remote == present ]]; then
  # A previous run pushed this branch (and may have opened the PR);
  # continue from the remote, never from a second fresh base.
  mkdir -p "$root"
  git -C "$repo" worktree add --quiet -b "$branch" "$dir" "origin/$branch"
  echo "note: $branch already exists on origin; continuing from it" >&2
else
  mkdir -p "$root"
  git -C "$repo" worktree add --quiet -b "$branch" "$dir" "origin/$base"
fi
if [[ $remote == present ]]; then
  git -C "$dir" branch --set-upstream-to "origin/$branch" >/dev/null
fi
local_head="$(git -C "$dir" rev-parse HEAD)"
echo "branch=$branch base=origin/$base@${base_sha:0:12} remote_branch=$([[ $remote == present ]] && echo yes || echo no) local_head=${local_head:0:12}"
echo "$dir"
