#!/usr/bin/env bash
# 本地一键打包：版本校验 + 源码包 + VSIX + 文档站 + 控制面镜像 + 校验和。
#
# 用法：
#   ./scripts/package_all.sh                  # 全量（默认构建镜像但不推送）
#   ./scripts/package_all.sh --no-image       # 跳过容器镜像
#   ./scripts/package_all.sh --no-docs        # 跳过 MkDocs
#   ./scripts/package_all.sh --skip-check     # 跳过 check.sh
#   ./scripts/package_all.sh --push           # buildx 多架构推送 GHCR（需先 docker login ghcr.io）
#   ./scripts/package_all.sh --out dist/rc1   # 自定义输出目录
#
# 产物默认在 dist/<version>/，已被 .gitignore 忽略。
# 正式发版仍走「打 tag → GitHub Release / release.yml」；本脚本用于本地验收与离线交付。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
IMAGE_BASE="${IMAGE_BASE:-ghcr.io/alexanderj-carter/computedeck}"
OUT="${OUT_DIR:-$ROOT/dist/$VERSION}"
WITH_IMAGE=1
WITH_DOCS=1
WITH_CHECK=1
PUSH=0
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"

usage() {
  sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-image) WITH_IMAGE=0 ;;
    --no-docs) WITH_DOCS=0 ;;
    --skip-check) WITH_CHECK=0 ;;
    --push) PUSH=1 ;;
    --platforms) PLATFORMS="${2:?--platforms 需要参数}"; shift ;;
    --out) OUT="${2:?--out 需要参数}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

cd "$ROOT"
mkdir -p "$OUT"
echo "== ComputeDeck $VERSION → $OUT"

if [[ ! -x "$PY" ]]; then
  echo "找不到 Python：$PY（先 uv venv .venv && uv pip install -r requirements.txt）" >&2
  exit 1
fi

if [[ "$WITH_CHECK" == "1" ]]; then
  echo "== 版本一致性 + 离线检查 =="
  "$PY" scripts/release_notes.py --check "$VERSION"
  ./scripts/check.sh
fi

if [[ -n "$(git status --porcelain 2>/dev/null || true)" ]]; then
  echo "!! 工作区有未提交改动：源码包只包含 HEAD，请确认是否先提交。" >&2
fi

echo "== 源码包 =="
SRC="$OUT/computedeck-$VERSION.tar.gz"
git archive --format=tar.gz --prefix="computedeck-$VERSION/" -o "$SRC" HEAD
echo "   $SRC"

echo "== VS Code 扩展（VSIX） =="
VSIX="$OUT/computedeck-vscode-$VERSION.vsix"
(
  cd extensions/vscode
  npx --yes @vscode/vsce package --allow-missing-repository --out "$VSIX"
)
echo "   $VSIX"

if [[ "$WITH_DOCS" == "1" ]]; then
  if [[ -x "$ROOT/.venv/bin/mkdocs" ]]; then
    echo "== 文档站 =="
    "$ROOT/.venv/bin/mkdocs" build --strict -d "$OUT/help"
    echo "   $OUT/help/index.html"
  else
    echo "!! 跳过文档：未安装 mkdocs（uv pip install -r requirements-docs.txt）" >&2
  fi
fi

if [[ "$WITH_IMAGE" == "1" ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "!! 跳过镜像：未找到 docker" >&2
  elif [[ "$PUSH" == "1" ]]; then
    echo "== 容器镜像（push $PLATFORMS） =="
    docker buildx build \
      --platform "$PLATFORMS" \
      --push \
      --build-arg "VERSION=$VERSION" \
      --build-arg "VCS_REF=$(git rev-parse HEAD)" \
      --build-arg "BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      -t "$IMAGE_BASE:$VERSION" \
      -t "$IMAGE_BASE:latest" \
      .
  else
    echo "== 容器镜像（本地 load） =="
    docker buildx build \
      --load \
      --build-arg "VERSION=$VERSION" \
      --build-arg "VCS_REF=$(git rev-parse HEAD)" \
      --build-arg "BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      -t "$IMAGE_BASE:$VERSION" \
      -t "computedeck:$VERSION" \
      .
    echo "   镜像：$IMAGE_BASE:$VERSION · computedeck:$VERSION"
  fi
fi

echo "== 校验和 =="
SUMS="$OUT/SHA256SUMS"
: > "$SUMS"
(
  cd "$OUT"
  for f in *; do
    [[ "$f" == "SHA256SUMS" ]] && continue
    [[ -f "$f" ]] || continue
    if command -v shasum >/dev/null 2>&1; then
      shasum -a 256 "$f" >> "$SUMS"
    else
      sha256sum "$f" >> "$SUMS"
    fi
  done
)
echo "   $SUMS"

echo
echo "== 完成 =="
ls -lh "$OUT"
echo
echo "安装： code --install-extension $VSIX"
echo "镜像： docker run --rm -p 127.0.0.1:8787:8787 $IMAGE_BASE:$VERSION"
