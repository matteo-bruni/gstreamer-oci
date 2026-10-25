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

# 2. INTERACTIVE PROMPTS
echo ""
echo "=== Manual Release Configuration ==="

prompt_for() {
    local prompt_text="$1"
    local default_value="$2"
    local var_name="$3"
    local input

    read \
        -r \
        -p "${prompt_text} [${default_value}]: " \
        input
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

    read \
        -r \
        -p "${prompt_text} [${default_value}]: " \
        input
    input=${input:-${default_value}}
    
    case "${input,,}" in
        y|yes) printf -v "${var_name}" '%s' true ;;
        n|no) printf -v "${var_name}" '%s' false ;;
        *) echo 'Please enter y or n' >&2; exit 1 ;;
    esac
}

validate_choice() {
    local value="$1"
    local field_name="$2"
    shift 2
    local allowed

    for allowed in "$@"; do
        if [ "${value}" = "${allowed}" ]; then
            return 0
        fi
    done

    echo "Fatal error: unsupported ${field_name} '${value}'. Allowed values: $*." >&2
    exit 1
}

prompt_for "GStreamer version" "1.28.8" GSTREAMER_VERSION
prompt_for "Release revision (1 = no suffix, 2 = r2, ...)" "${RELEASE_REVISION:-1}" RELEASE_REVISION
prompt_for "Python versions (space-separated)" "3.12" PYTHON_VERSIONS
prompt_for "Base image" "ubuntu" BASE_IMAGE
prompt_for "Base tag" "24.04" BASE_TAG
GSTREAMER_BUILD_PROFILE=base
echo "Public releases use the base profile; minimal/full remain available through just build."
prompt_for "Variant (free/non-free)" "free" VARIANT
prompt_for "Image target (runtime/dev)" "runtime" IMAGE_TARGET
prompt_for "Build type (release/debug/debugoptimized)" "release" BUILD_TYPE
prompt_for "uv version" "0.13.5" UV_VERSION
prompt_for_bool "Is this a Dry Run? (y/N)" "N" DRY_RUN
prompt_for_bool "Mark as Pre-release? (y/N)" "N" PRERELEASE
prompt_for_bool "Create as Draft (hidden until manually published)? (y/N)" "N" DRAFT

validate_choice "$VARIANT" "variant" free non-free
validate_choice "$IMAGE_TARGET" "image target" runtime dev
validate_choice "${BUILD_TYPE}" "build type" release debug debugoptimized

if [[ "$DRY_RUN" == false ]]; then
    auth_status="$(gh auth status 2>&1)"
    if [[ "$auth_status" != *write:packages* ]]; then
        echo 'The local release token needs write:packages; run gh auth refresh -h github.com -s write:packages' >&2
        exit 1
    fi
fi

export GSTREAMER_VERSION PYTHON_VERSIONS BASE_IMAGE BASE_TAG GSTREAMER_BUILD_PROFILE
export VARIANT IMAGE_TARGET BUILD_TYPE UV_VERSION DRY_RUN PRERELEASE DRAFT RELEASE_REVISION
exec bash "$(dirname "$0")/release.sh"
