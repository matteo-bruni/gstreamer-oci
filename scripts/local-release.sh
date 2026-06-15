#!/usr/bin/env bash
set -euo pipefail

# 1. DEPENDENCY CHECK (Fail Fast)
echo "Checking required tools..."
for cmd in docker just jq gh git sha256sum; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "Fatal error: Command '$cmd' is not installed or not in PATH." >&2
        exit 1
    fi
done

if ! gh auth status &>/dev/null; then
    echo "Error: You are not authenticated with GitHub CLI. Run 'gh auth login' first." >&2
    exit 1
fi

GH_AUTH_STATUS="$(gh auth status 2>&1)"

# 2. INTERACTIVE PROMPTS
echo ""
echo "=== Manual Release Configuration ==="

prompt_for() {
    local prompt_text="$1"
    local default_value="$2"
    local var_name="$3"
    local input

    read -r -p "${prompt_text} [${default_value}]: " input
    if [[ -z "${input}" ]]; then
        printf -v "${var_name}" "%s" "${default_value}"
    else
        printf -v "${var_name}" "%s" "${input}"
    fi
}

prompt_for_bool() {
    local prompt_text="$1"
    local default_value="$2" # "Y" or "N"
    local var_name="$3"
    local input

    read -r -p "${prompt_text} [${default_value}]: " input
    input=${input:-${default_value}}
    
    if [[ "${input}" =~ ^[Yy] ]]; then
        printf -v "${var_name}" "true"
    else
        printf -v "${var_name}" "false"
    fi
}

prompt_for "GStreamer version" "1.28.3" GSTREAMER_VERSION
prompt_for "Python versions (space-separated)" "3.12" PYTHON_VERSIONS
prompt_for "Base image" "ubuntu" BASE_IMAGE
prompt_for "Base tag" "24.04" BASE_TAG
prompt_for "GStreamer build profile" "base" GSTREAMER_BUILD_PROFILE
prompt_for_bool "Enable non-free dependencies? (y/N)" "N" GSTREAMER_ENABLE_NON_FREE
prompt_for "uv version" "0.11.16" UV_VERSION
prompt_for_bool "Is this a Dry Run? (y/N)" "N" DRY_RUN
prompt_for_bool "Mark as Pre-release? (y/N)" "N" PRERELEASE
prompt_for_bool "Create as Draft (hidden until manually published)? (y/N)" "N" DRAFT

BUILD_TYPE="release"
BUILD_TYPE_EXPLANATION="release: optimized build, LTO enabled in the Dockerfile, no +debug local wheel suffix, intended for redistribution"

# GitHub context mapping
GH_REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
GH_USER="$(gh api user --jq .login)"
OWNER_LC="$(echo "$GH_REPO" | cut -d'/' -f1 | tr '[:upper:]' '[:lower:]')"
GHCR_IMAGE_NAME="${GHCR_IMAGE_NAME:-gstreamer}"
IMAGE_REPOSITORY="ghcr.io/${OWNER_LC}/${GHCR_IMAGE_NAME}"

# Generate release identifiers
NON_FREE_SUFFIX=""
NON_FREE_LABEL=""
if [ "${GSTREAMER_ENABLE_NON_FREE}" = "true" ]; then
    NON_FREE_SUFFIX="-non-free"
    NON_FREE_LABEL=", non-free"
fi

RELEASE_TAG="gst-python-binding-${GSTREAMER_VERSION}-${BASE_IMAGE}-${BASE_TAG}-${GSTREAMER_BUILD_PROFILE}${NON_FREE_SUFFIX}"
RELEASE_NAME="GStreamer Python binding ${GSTREAMER_VERSION} (${BASE_IMAGE}:${BASE_TAG}, ${GSTREAMER_BUILD_PROFILE}${NON_FREE_LABEL})"
SHORT_SHA="$(git rev-parse --short=12 HEAD)"
GIT_SHA="$(git rev-parse HEAD)"

# 3. RELEASE CONFLICT CHECK (Fail Fast)
if [ "${DRY_RUN}" != "true" ]; then
    if [[ "${GH_AUTH_STATUS}" != *"write:packages"* ]]; then
        echo "Fatal error: the active GitHub CLI token does not have 'write:packages'." >&2
        echo "Your current scopes are:" >&2
        echo "${GH_AUTH_STATUS}" >&2
        echo "" >&2
        echo "Refresh the token with:" >&2
        echo "  gh auth refresh -h github.com -s write:packages" >&2
        echo "" >&2
        echo "Or log in again with a token that includes at least: repo, read:org, write:packages" >&2
        exit 1
    fi

    echo "Logging Docker in to GHCR as ${GH_USER}..."
    gh auth token | docker login ghcr.io -u "${GH_USER}" --password-stdin >/dev/null

    echo "Checking if release tag '${RELEASE_TAG}' already exists..."
    
    # Verifica se la release esiste già interrogando l'API via gh cli
    if gh release view "${RELEASE_TAG}" &>/dev/null; then
        echo "Fatal error: A GitHub Release with tag '${RELEASE_TAG}' already exists." >&2
        echo "Please delete it first or use different parameters." >&2
        exit 1
    fi
    
    # Verifica se il tag git esiste localmente o sul remote
    if git ls-remote --exit-code --tags origin "${RELEASE_TAG}" &>/dev/null || git rev-parse -q --verify "refs/tags/${RELEASE_TAG}" &>/dev/null; then
        echo "Fatal error: Git tag '${RELEASE_TAG}' already exists." >&2
        echo "Please delete the tag locally and remotely before proceeding." >&2
        exit 1
    fi
    echo "Release tag is available."
fi

ASSETS_DIR="release-assets"
NOTES_FILE="${ASSETS_DIR}/release-notes.md"

echo ""
echo "========================================"
echo "Starting build for Release: $RELEASE_TAG"
echo "GHCR Repository: $IMAGE_REPOSITORY"
echo "Build profile: $GSTREAMER_BUILD_PROFILE | Non-free: $GSTREAMER_ENABLE_NON_FREE"
echo "Dry Run: $DRY_RUN | Pre-release: $PRERELEASE | Draft: $DRAFT"
echo "========================================"

rm -rf "${ASSETS_DIR}"
mkdir -p "${ASSETS_DIR}"

{
  echo "Manual release generated via local script."
  echo
  echo "- GStreamer version: ${GSTREAMER_VERSION}"
  echo "- Base image: ${BASE_IMAGE}:${BASE_TAG}"
    echo "- Build profile: ${GSTREAMER_BUILD_PROFILE}"
    echo "- Non-free: ${GSTREAMER_ENABLE_NON_FREE}"
    echo "- Build type: ${BUILD_TYPE}"
  echo "- Python versions: ${PYTHON_VERSIONS}"
  echo "- Commit: ${GIT_SHA}"
  echo "- GHCR repository: ${IMAGE_REPOSITORY}"
  echo
    echo "## Active options"
    echo
    echo "- Build type: ${BUILD_TYPE}"
    echo "- Build type behavior: ${BUILD_TYPE_EXPLANATION}"
    echo "- GStreamer build profile: ${GSTREAMER_BUILD_PROFILE}"
    echo "- Non-free dependencies enabled: ${GSTREAMER_ENABLE_NON_FREE}"
    echo
  echo "## Published variants"
} > "${NOTES_FILE}"

read -r -a py_versions_array <<< "${PYTHON_VERSIONS}"

# 4. BUILD AND ASSET PREPARATION
for py_ver in "${py_versions_array[@]}"; do
    echo "Building Python ${py_ver} variant..."
    just build-release "${BASE_IMAGE}" "${BASE_TAG}" "${GSTREAMER_VERSION}" "${GSTREAMER_BUILD_PROFILE}" "${GSTREAMER_ENABLE_NON_FREE}" "${py_ver}" "${UV_VERSION}"

    local_tag="$(just image-tag release "${BASE_IMAGE}" "${BASE_TAG}" "${GSTREAMER_VERSION}" "${GSTREAMER_BUILD_PROFILE}" "${GSTREAMER_ENABLE_NON_FREE}" "${py_ver}")"
    image_suffix="${local_tag#*:}"
    image_tag="${IMAGE_REPOSITORY}:${image_suffix}"
    image_tag_sha="${image_tag}-sha-${SHORT_SHA}"
    variant_dir="${ASSETS_DIR}/py${py_ver}"
    mkdir -p "${variant_dir}"

    if [ "${DRY_RUN}" != "true" ]; then
        echo "Pushing images to GHCR..."
        docker tag "${local_tag}" "${image_tag}"
        docker tag "${local_tag}" "${image_tag_sha}"
        docker push "${image_tag}"
        docker push "${image_tag_sha}"
    else
        echo "DRY RUN: Skipping GHCR push."
    fi

    echo "Extracting wheel..."
    container_id="$(docker create "${local_tag}")"
    docker cp "${container_id}:/opt/wheel/." "${variant_dir}"
    docker rm -v "${container_id}"

    wheel_path="$(find "${variant_dir}" -maxdepth 1 -name 'gst_python_binding-*.whl' -print -quit)"
    if [ -z "${wheel_path}" ]; then
        echo "No wheel found in ${variant_dir}" >&2
        exit 1
    fi

    original_wheel="$(basename "${wheel_path}")"
    wheel_asset="${original_wheel}"
    checksum_asset="${original_wheel}.sha256"
    metadata_asset="py${py_ver}-build-metadata.json"

    mv "${wheel_path}" "${variant_dir}/${wheel_asset}"

    (
        cd "${variant_dir}"
        sha256sum "${wheel_asset}" > "${checksum_asset}"
    )

    jq -n \
      --arg py_versions_str "${PYTHON_VERSIONS}" \
      --arg wheel_file "${wheel_asset}" \
      --arg img_tag "${image_tag}" \
      --arg img_sha "${image_tag_sha}" \
      --arg rel_tag "${RELEASE_TAG}" \
            --arg build_profile "${GSTREAMER_BUILD_PROFILE}" \
            --arg non_free "${GSTREAMER_ENABLE_NON_FREE}" \
            --arg build_type "${BUILD_TYPE}" \
            --arg build_type_explanation "${BUILD_TYPE_EXPLANATION}" \
      --arg py_ver "${py_ver}" \
      --arg git_sha "${GIT_SHA}" \
      --arg short_sha "${SHORT_SHA}" \
      --arg repo "${GH_REPO}" \
      '{
        requested_python_versions: ($py_versions_str | split(" ")),
        wheel_file: $wheel_file,
        image_tag: $img_tag,
        image_tag_sha: $img_sha,
        release_tag: $rel_tag,
                build_profile: $build_profile,
                non_free: $non_free,
        python_version: $py_ver,
                build_type: $build_type,
                build_type_explanation: $build_type_explanation,
        git_sha: $git_sha,
        git_short_sha: $short_sha,
        repository: $repo
            }' > "${variant_dir}/${metadata_asset}"

    {
        echo
        echo "### Python ${py_ver}"
        echo
        echo "- OCI image: ${image_tag}"
        echo "- Build profile: ${GSTREAMER_BUILD_PROFILE}"
        echo "- Non-free: ${GSTREAMER_ENABLE_NON_FREE}"
        echo "- Build type: ${BUILD_TYPE}"
        echo "- Build type behavior: ${BUILD_TYPE_EXPLANATION}"
        echo "- Wheel asset: ${wheel_asset}"
    } >> "${NOTES_FILE}"
done

# 5. GITHUB RELEASE CREATION
if [ "${DRY_RUN}" != "true" ]; then
    echo "Publishing GitHub Release..."
    
    # Create an array with all asset file paths
    mapfile -t ASSET_FILES < <(find "${ASSETS_DIR}" -type f -not -name 'release-notes.md')

    # Build the gh CLI command flags
    GH_FLAGS=(
        "--title" "${RELEASE_NAME}"
        "--notes-file" "${NOTES_FILE}"
    )
    
    if [ "${PRERELEASE}" = "true" ]; then
        GH_FLAGS+=("--prerelease")
    fi
    
    if [ "${DRAFT}" = "true" ]; then
        GH_FLAGS+=("--draft")
    fi

    gh release create "${RELEASE_TAG}" "${GH_FLAGS[@]}" "${ASSET_FILES[@]}"
    
    echo "Release ${RELEASE_TAG} successfully created!"
else
    echo "DRY RUN complete. No release was created. Prepared assets are in ${ASSETS_DIR}."
fi