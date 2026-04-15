#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f VERSION ]]; then
    echo "VERSION file not found"
    exit 1
fi

VERSION_VALUE=${1:-$(tr -d '[:space:]' < VERSION)}
TAG="v${VERSION_VALUE#v}"

if [[ ! "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Version must look like 0.1.0 or v0.1.0"
    exit 1
fi

if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "Tag $TAG already exists"
    exit 1
fi

git tag -a "$TAG" -m "Release $TAG"
git push origin "$TAG"

echo "Created and pushed $TAG"
