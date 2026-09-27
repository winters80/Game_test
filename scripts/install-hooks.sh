#!/bin/sh
# Install local git hooks for System Breaker.
#
# Run from the repo root:
#   ./scripts/install-hooks.sh
#
# This copies scripts/hooks/* into .git/hooks/, overwriting any existing
# hook of the same name. Hooks are local-only — git doesn't sync them
# automatically — so each collaborator runs this once after cloning.

set -e

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$REPO_ROOT" ]; then
    echo "Error: not inside a git repository." >&2
    exit 1
fi

SRC="$REPO_ROOT/scripts/hooks"
# --git-path resolves correctly from worktrees (where .git is a file) and
# honours core.hooksPath.
DEST="$(cd "$REPO_ROOT" && git rev-parse --path-format=absolute --git-path hooks)"

if [ ! -d "$SRC" ]; then
    echo "Error: $SRC does not exist." >&2
    exit 1
fi

mkdir -p "$DEST"

for hook in "$SRC"/*; do
    name="$(basename "$hook")"
    target="$DEST/$name"
    cp "$hook" "$target"
    chmod +x "$target"
    echo "  installed: $name"
done

echo ""
echo "Hooks installed. The pre-push hook will now run test_characters.py"
echo "and test_quests.py before every push, and abort on failure."
