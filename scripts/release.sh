#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/build-common.sh"
cd "$(dirname "$0")/.."

BUILD_TYPE=${BUILD_TYPE:-release}
DRAFT=${DRAFT:-false}
PRERELEASE=${PRERELEASE:-false}
DRY_RUN=${DRY_RUN:-false}
UV_VERSION=${UV_VERSION:-0.13.5}
if [[ "$DRY_RUN" == true ]]; then unset BUILD_CACHE_TO; fi
read \
    -r \
    -a versions \
    <<< "$PYTHON_VERSIONS"
[[ ${#versions[@]} -gt 0 ]] || { echo 'At least one Python version is required' >&2; exit 1; }
for py in "${versions[@]}"; do
    validate_build_args "$BUILD_TYPE" "$BASE_IMAGE" "$BASE_TAG" "$GSTREAMER_VERSION" \
        "$GSTREAMER_BUILD_PROFILE" "$VARIANT" "$IMAGE_TARGET" "$py"
done
for value in "$DRAFT" "$PRERELEASE" "$DRY_RUN"; do
    [[ "$value" == true || "$value" == false ]] || exit 1
done
if [[ "$DRY_RUN" == false && "$GSTREAMER_BUILD_PROFILE" != base ]]; then
    echo 'Only the base profile may be published; use just build for minimal/full or a dry run' >&2
    exit 1
fi
check_gstreamer_tag
repo=${GITHUB_REPOSITORY:-$(gh repo view \
    --json nameWithOwner \
    --jq .nameWithOwner)}
commit="$(git rev-parse HEAD)"
short_sha=${commit:0:12}
owner=${repo%%/*}
image_repository="ghcr.io/${owner,,}/${GHCR_IMAGE_NAME:-gstreamer}"
[[ "$image_repository" =~ ^ghcr.io/[a-z0-9._-]+/[a-z0-9._-]+$ ]] || exit 1
suffix=''
[[ "$BUILD_TYPE" == release ]] || suffix+="-$BUILD_TYPE"
[[ "$IMAGE_TARGET" == runtime ]] || suffix+='-dev'
[[ "$VARIANT" == free ]] || suffix+='-non-free'
release_tag=${CUSTOM_RELEASE_TAG:-gst-python-binding-$GSTREAMER_VERSION-$BASE_IMAGE-$BASE_TAG-$GSTREAMER_BUILD_PROFILE$suffix}
release_name=${CUSTOM_RELEASE_NAME:-GStreamer Python binding $GSTREAMER_VERSION ($BASE_IMAGE:$BASE_TAG, $GSTREAMER_BUILD_PROFILE$suffix)}
git check-ref-format "refs/tags/$release_tag"
[[ "$release_name" != *$'\n'* ]] || { echo 'Release title must be a single line' >&2; exit 1; }

if [[ "$DRY_RUN" == false ]]; then
    [[ -z "$(git status --porcelain)" ]] || { echo 'Commit or remove local changes before publishing' >&2; exit 1; }
    remote_commit="$(gh api "repos/$repo/commits/$commit" --jq .sha)"
    [[ "$remote_commit" == "$commit" ]] || { echo 'Push the source commit to GitHub before publishing' >&2; exit 1; }
    existing_tags="$(gh release list \
        --repo "$repo" \
        --limit 1000 \
        --json tagName \
        --jq '.[].tagName')"
    if grep -Fxq -- "$release_tag" <<< "$existing_tags"; then
        echo "Release already exists: $release_tag" >&2; exit 1
    fi
    remote_tags="$(git ls-remote --tags "https://github.com/$repo.git" "refs/tags/$release_tag")"
    if [[ -n "$remote_tags" ]] || git show-ref \
        --verify \
        --quiet \
        "refs/tags/$release_tag"; then
        echo "Tag already exists: $release_tag" >&2; exit 1
    fi
fi

assets=release-assets
rm -rf "$assets"
mkdir -p "$assets/upload"
notes="$assets/release-notes.md"
{
    printf 'GStreamer %s\n\n' "$GSTREAMER_VERSION"
    printf -- '- Base: %s:%s\n- Profile: %s\n- Build type: %s\n- Image target: %s\n- Variant: %s\n- Commit: %s\n- Python: %s\n' \
        "$BASE_IMAGE" "$BASE_TAG" "$GSTREAMER_BUILD_PROFILE" "$BUILD_TYPE" "$IMAGE_TARGET" "$VARIANT" "$commit" "$PYTHON_VERSIONS"
    printf -- '- uv image version: %s\n- Compilation jobs: %s\n' "$UV_VERSION" "${BUILD_JOBS:-6}"
    echo '- release enables LTO; debug adds +debug to the wheel; debugoptimized keeps optimized debug information.'
    echo '- minimal includes GStreamer/Python/introspection without GTK or gst-libav; base adds GTK and gst-libav; full also adds Rust plugins (excluding csound).'
    echo '- non-free enables GPL codec builds including x264/x265 and installs the requested Intel driver.'
    echo '- dev retains the compiler toolchain and source; runtime installs all Python dependencies from prebuilt wheels.'
    echo '- Wheels require the native libraries and typelibs in the matching Ubuntu image.'
} > "$notes"

# Prepare and verify every variant before publishing any image.
for py in "${versions[@]}"; do
    local_tag="$(bash scripts/image-tag.sh \
        "$BUILD_TYPE" \
        "$BASE_IMAGE" \
        "$BASE_TAG" \
        "$GSTREAMER_VERSION" \
        "$GSTREAMER_BUILD_PROFILE" \
        "$VARIANT" \
        "$IMAGE_TARGET" \
        "$py")"
    cache_key="$BASE_TAG-$GSTREAMER_BUILD_PROFILE-$VARIANT-$BUILD_TYPE-py$py"
    if [[ "${GITHUB_ACTIONS:-false}" == true && "$DRY_RUN" == false ]]; then
        export BUILD_CACHE_FROM="type=registry,ref=$image_repository-cache:$cache_key"
        export BUILD_CACHE_TO="$BUILD_CACHE_FROM,mode=max"
    fi
    bash scripts/build.sh \
        "$BUILD_TYPE" \
        "$BASE_IMAGE" \
        "$BASE_TAG" \
        "$GSTREAMER_VERSION" \
        "$GSTREAMER_BUILD_PROFILE" \
        "$VARIANT" \
        "$IMAGE_TARGET" \
        "$py" \
        "$UV_VERSION"
    bash scripts/verify-image.sh "$local_tag"
    python_assets="$assets/py$py"
    mkdir -p "$python_assets"
    container_id="$(docker create "$local_tag")"
    trap 'docker rm -v "$container_id" >/dev/null' EXIT
    docker cp "$container_id:/opt/wheel/." "$python_assets"
    docker rm -v "$container_id" >/dev/null
    trap - EXIT
    mv "$python_assets/build-environment.json" "$python_assets/py$py-build-environment.json"
    for wheel in "$python_assets/"*.whl; do
        (cd "$python_assets"; sha256sum "$(basename "$wheel")" > "$(basename "$wheel").sha256")
    done
    image_tag="$image_repository:${local_tag#*:}"
    binding_wheels=("$python_assets/"gst_python_binding-*.whl)
    [[ ${#binding_wheels[@]} == 1 && -f "${binding_wheels[0]}" ]] || { echo 'Expected exactly one binding wheel' >&2; exit 1; }
    jq \
        -n \
        --arg image_tag "$image_tag" \
        --arg image_tag_sha "$image_tag-sha-$short_sha" \
        --arg git_sha "$commit" \
        --arg python_version "$py" \
        --arg release_tag "$release_tag" \
        --arg build_profile "$GSTREAMER_BUILD_PROFILE" \
        --arg variant "$VARIANT" \
        --arg image_target "$IMAGE_TARGET" \
        --arg build_type "$BUILD_TYPE" \
        --arg repository "$repo" \
        --arg uv_version "$UV_VERSION" \
        --arg base_image "$BASE_IMAGE" \
        --arg base_tag "$BASE_TAG" \
        --arg git_short_sha "$short_sha" \
        --arg versions "$PYTHON_VERSIONS" \
        --arg wheel_file "$(basename "${binding_wheels[0]}")" \
        --slurpfile environment "$python_assets/py$py-build-environment.json" \
        '{image_tag:$image_tag,image_tag_sha:$image_tag_sha,git_sha:$git_sha,git_short_sha:$git_short_sha,
          python_version:$python_version,release_tag:$release_tag,build_profile:$build_profile,
          variant:$variant,image_target:$image_target,build_type:$build_type,repository:$repository,
          uv_version:$uv_version,base_image:$base_image,base_tag:$base_tag,
          requested_python_versions:($versions|split(" ")|map(select(length>0))),wheel_file:$wheel_file,build:$environment[0]}' \
        > "$python_assets/py$py-build-metadata.json"
    for asset in "$python_assets/"*; do
        dest="$assets/upload/$(basename "$asset")"
        if [[ -e "$dest" ]]; then
            cmp "$asset" "$dest" || { echo "Conflicting release asset: $dest" >&2; exit 1; }
        else
            cp "$asset" "$dest"
        fi
    done
    printf '\n### Python %s\n\n- Image: %s\n- Commit tag: %s-sha-%s\n' "$py" "$image_tag" "$image_tag" "$short_sha" >> "$notes"
done
printf '\n%s\n' "${EXTRA_RELEASE_NOTES:-}" >> "$notes"
if [[ "$DRY_RUN" == true ]]; then
    echo "Dry run complete: all variants verified; assets in $assets"
    exit 0
fi
[[ "$(git rev-parse HEAD)" == "$commit" && -z "$(git status --porcelain)" ]] || {
    echo 'Source commit or working tree changed during the builds; refusing publication' >&2
    exit 1
}

if [[ "${GITHUB_ACTIONS:-false}" != true ]]; then
    gh auth token | docker login ghcr.io \
        -u "$(gh api user --jq .login)" \
        --password-stdin \
        >/dev/null
fi
for py in "${versions[@]}"; do
    local_tag="$(bash scripts/image-tag.sh \
        "$BUILD_TYPE" \
        "$BASE_IMAGE" \
        "$BASE_TAG" \
        "$GSTREAMER_VERSION" \
        "$GSTREAMER_BUILD_PROFILE" \
        "$VARIANT" \
        "$IMAGE_TARGET" \
        "$py")"
    image_tag="$image_repository:${local_tag#*:}"
    docker tag "$local_tag" "$image_tag"
    docker tag "$local_tag" "$image_tag-sha-$short_sha"
    docker push "$image_tag"
    docker push "$image_tag-sha-$short_sha"
done
flags=(
    --repo "$repo"
    --target "$commit"
    --title "$release_name"
    --notes-file "$notes"
)
[[ "$PRERELEASE" == false ]] || flags+=(--prerelease)
[[ "$DRAFT" == false ]] || flags+=(--draft)
gh release create "$release_tag" "${flags[@]}" "$assets/upload/"*
