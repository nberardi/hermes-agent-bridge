#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

readonly PROJECT_VERSION="0.1.0"
readonly RELEASE_REPOSITORY="nberardi/hermes-agent-bridge"
readonly IMAGE_REPOSITORY="ghcr.io/nberardi/hermes-agent-bridge"
readonly INSTALL_ROOT="/opt/hermes-agent-bridge"
readonly DEFAULT_ENV_FILE="/etc/hermes-agent-bridge.env"
readonly PROXMOX_STATE="/etc/hermes-agent-bridge-proxmox.conf"
readonly DEBIAN_LXC_SCRIPT="https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/ct/debian.sh"
readonly UV_VERSION="0.8.15"
readonly PYTHON_VERSION="3.12.11"

MODE=""
VERSION="v${PROJECT_VERSION}"
ENV_FILE="$DEFAULT_ENV_FILE"
NON_INTERACTIVE=0
TEMP_DIR=""
CONFIG_BACKUP=""
CREATED_CT=""
RESTORE_ON_EXIT=0
CONFIG_WAS_NEW=0

usage() {
    cat <<'EOF'
Usage: sudo ./install.sh <docker|native|proxmox> [options]

Options:
  --version vX.Y.Z   Install this immutable release (default: installer version)
  --env-file PATH    Read/write configuration at PATH
  --non-interactive  Require all settings from an existing env file/environment
  -h, --help         Show this help
EOF
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

log() {
    printf '==> %s\n' "$*" >&2
}

cleanup() {
    if [[ -n "$CREATED_CT" ]]; then
        destroy_failed_container "$CREATED_CT"
    fi
    if [[ $RESTORE_ON_EXIT -eq 1 ]]; then
        if [[ -n "$CONFIG_BACKUP" && -f "$CONFIG_BACKUP" ]]; then
            install -o root -g root -m 0600 "$CONFIG_BACKUP" "$ENV_FILE"
        elif [[ $CONFIG_WAS_NEW -eq 1 ]]; then
            rm -f -- "$ENV_FILE"
        fi
    fi
    if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
        rm -rf -- "$TEMP_DIR"
    fi
}

trap cleanup EXIT

parse_args() {
    [[ $# -gt 0 ]] || { usage >&2; exit 2; }
    MODE="$1"
    shift
    case "$MODE" in
        docker | native | proxmox) ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --version)
                [[ $# -ge 2 ]] || die "--version requires a value"
                VERSION="$2"
                shift 2
                ;;
            --env-file)
                [[ $# -ge 2 ]] || die "--env-file requires a path"
                ENV_FILE="$2"
                shift 2
                ;;
            --non-interactive)
                NON_INTERACTIVE=1
                shift
                ;;
            -h | --help)
                usage
                exit 0
                ;;
            *) die "unknown option: $1" ;;
        esac
    done
    [[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || \
        die "version must have the form vX.Y.Z"
    [[ "$ENV_FILE" =~ ^/[a-zA-Z0-9_./-]+$ ]] || \
        die "--env-file must be an absolute path using letters, numbers, '.', '_', or '-'"
    [[ $EUID -eq 0 ]] || die "run this installer as root (sudo)"
}

readonly CONFIG_KEYS=(
    SITE PUBLIC_HOSTNAMES HERMES_KANBAN_BOARDS ALLOWED_ORIGINS
    CF_ACCESS_TEAM_DOMAIN CF_ACCESS_AUD HERMES_DASHBOARD_URL
    HERMES_DASHBOARD_TOKEN HERMES_API_URL HERMES_API_KEY BIND_HOST BIND_PORT
)

cfg_set() {
    printf -v "CFG_$1" '%s' "$2"
}

cfg_get() {
    local name="CFG_$1"
    printf '%s' "${!name-}"
}

is_config_key() {
    local wanted="$1" key
    for key in "${CONFIG_KEYS[@]}"; do
        [[ "$key" == "$wanted" ]] && return 0
    done
    return 1
}

load_config() {
    local path="$1" line key value env_value
    if [[ -f "$path" ]]; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
            key="${line%%=*}"
            value="${line#*=}"
            is_config_key "$key" && cfg_set "$key" "$value"
        done < "$path"
    fi
    for key in "${CONFIG_KEYS[@]}"; do
        env_value="${!key-}"
        if [[ -n "$env_value" ]]; then
            cfg_set "$key" "$env_value"
        fi
    done
}

prompt_value() {
    local key="$1" label="$2" required="$3" default_value="${4-}" secret="${5-0}"
    local current answer=""
    current="$(cfg_get "$key")"
    if [[ -z "$current" && -n "$default_value" ]]; then
        current="$default_value"
    fi
    if [[ $NON_INTERACTIVE -eq 1 ]]; then
        if [[ "$required" == 1 && -z "$current" ]]; then
            die "$key is required with --non-interactive"
        fi
        [[ "$current" != *$'\n'* && "$current" != *$'\r'* ]] || \
            die "$key may not contain a line break"
        cfg_set "$key" "$current"
        return
    fi
    if [[ "$secret" == 1 ]]; then
        if [[ -n "$current" ]]; then
            read -r -s -p "$label [press Enter to keep the existing value]: " answer
        else
            read -r -s -p "$label: " answer
        fi
        printf '\n'
    else
        if [[ -n "$current" ]]; then
            read -r -p "$label [$current]: " answer
        else
            read -r -p "$label: " answer
        fi
    fi
    if [[ -n "$answer" ]]; then
        current="$answer"
    fi
    if [[ "$required" == 1 && -z "$current" ]]; then
        die "$key is required"
    fi
    [[ "$current" != *$'\n'* && "$current" != *$'\r'* ]] || \
        die "$key may not contain a line break"
    cfg_set "$key" "$current"
}

configuration_wizard() {
    local path
    for path in "$@"; do
        load_config "$path"
    done
    prompt_value SITE "Site name" 1
    prompt_value PUBLIC_HOSTNAMES "Public MCP hostname(s), comma separated" 1
    prompt_value HERMES_KANBAN_BOARDS "Allowed Hermes kanban board slug(s), comma separated" 1
    prompt_value ALLOWED_ORIGINS "Allowed browser origin(s), comma separated" 0
    prompt_value CF_ACCESS_TEAM_DOMAIN "Cloudflare Access team domain" 1
    prompt_value CF_ACCESS_AUD "Cloudflare Access application AUD" 1 "" 1
    prompt_value HERMES_DASHBOARD_URL "Private Hermes dashboard URL" 1
    prompt_value HERMES_DASHBOARD_TOKEN "Hermes dashboard bearer token (optional)" 0 "" 1
    prompt_value HERMES_API_URL "Ask gateway URL (optional; blank disables Ask)" 0
    prompt_value HERMES_API_KEY "Ask gateway API key (optional)" 0 "" 1
    prompt_value BIND_HOST "Bind address" 1 "0.0.0.0"
    prompt_value BIND_PORT "Bind port" 1 "8080"
}

write_config() {
    local path="$1" target_dir key config_tmp
    target_dir="$(dirname -- "$path")"
    install -d -m 0755 "$target_dir"
    config_tmp="$(mktemp "${target_dir}/.hermes-agent-bridge.env.XXXXXX")"
    chmod 0600 "$config_tmp"
    {
        printf '# Managed by hermes-agent-bridge install.sh. Contains secrets.\n'
        for key in "${CONFIG_KEYS[@]}"; do
            printf '%s=%s\n' "$key" "$(cfg_get "$key")"
        done
    } > "$config_tmp"
    chown root:root "$config_tmp"
    mv -f "$config_tmp" "$path"
    chmod 0600 "$path"
}

sha256_check() {
    local checksum_file="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum -c "$checksum_file"
    else
        shasum -a 256 -c "$checksum_file"
    fi
}

download() {
    local url="$1" output="$2"
    curl --fail --location --proto '=https' --tlsv1.2 --retry 3 \
        --output "$output" "$url"
}

prepare_release() {
    local release_dir="$INSTALL_ROOT/releases/$VERSION"
    local bundle="hermes-agent-bridge-${VERSION}.tar.gz"
    local base_url="https://github.com/${RELEASE_REPOSITORY}/releases/download/${VERSION}"
    local staging="$INSTALL_ROOT/releases/.${VERSION}.staging.$$"
    install -d -m 0755 "$INSTALL_ROOT/releases"
    if [[ -d "$release_dir" ]]; then
        printf '%s\n' "$release_dir"
        return
    fi
    install -d -m 0755 "$staging"
    if [[ -f "$SCRIPT_DIR/$bundle" && -f "$SCRIPT_DIR/SHA256SUMS" ]]; then
        cp "$SCRIPT_DIR/$bundle" "$TEMP_DIR/$bundle"
        cp "$SCRIPT_DIR/SHA256SUMS" "$TEMP_DIR/SHA256SUMS"
    else
        log "Downloading release bundle $VERSION"
        download "$base_url/$bundle" "$TEMP_DIR/$bundle"
        download "$base_url/SHA256SUMS" "$TEMP_DIR/SHA256SUMS"
    fi
    (
        cd "$TEMP_DIR"
        grep "  ${bundle}$" SHA256SUMS > bundle.sha256 || \
            die "SHA256SUMS does not list $bundle"
        sha256_check bundle.sha256
    )
    tar -xzf "$TEMP_DIR/$bundle" --strip-components=1 -C "$staging"
    [[ -f "$staging/deploy/compose.yaml" && \
       -f "$staging/deploy/hermes-agent-bridge.service" && \
       -f "$staging/requirements-runtime.txt" ]] || \
        die "release bundle is missing required installation assets"
    mv "$staging" "$release_dir"
    printf '%s\n' "$release_dir"
}

switch_current() {
    local release_dir="$1"
    ln -sfn "$release_dir" "$INSTALL_ROOT/current.new"
    mv -Tf "$INSTALL_ROOT/current.new" "$INSTALL_ROOT/current"
}

existing_release() {
    if [[ -L "$INSTALL_ROOT/current" ]]; then
        readlink -f "$INSTALL_ROOT/current"
    fi
}

install_docker_engine() {
    local os_id version_codename answer=""
    # shellcheck source=/dev/null
    source /etc/os-release
    os_id="${ID:-}"
    version_codename="${VERSION_CODENAME:-}"
    [[ "$os_id" == debian || "$os_id" == ubuntu ]] || \
        die "Docker is required. Install Docker Engine and Compose v2, then rerun."
    if [[ $NON_INTERACTIVE -eq 0 ]]; then
        read -r -p "Docker is missing. Install Docker Engine and Compose from Docker's official repository? [y/N] " answer
        [[ "$answer" =~ ^[Yy]$ ]] || die "Docker Engine and Compose v2 are required"
    fi
    apt-get update
    apt-get install -y ca-certificates curl
    install -m 0755 -d /etc/apt/keyrings
    download "https://download.docker.com/linux/${os_id}/gpg" /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
        "$(dpkg --print-architecture)" "$os_id" "$version_codename" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

choose_docker_network() {
    local network="${HERMES_DOCKER_NETWORK:-hermes}" answer=""
    if [[ $NON_INTERACTIVE -eq 0 ]]; then
        read -r -p "Existing Docker network shared with Hermes [$network]: " answer
        [[ -z "$answer" ]] || network="$answer"
    fi
    [[ "$network" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || die "invalid Docker network name"
    docker network inspect "$network" >/dev/null 2>&1 || \
        die "Docker network '$network' does not exist; create it or choose the Hermes network"
    printf '%s\n' "$network"
}

docker_compose() {
    local release_dir="$1" network="$2"
    shift 2
    HERMES_BRIDGE_VERSION="$VERSION" \
    HERMES_DOCKER_NETWORK="$network" \
    HERMES_BRIDGE_ENV="$ENV_FILE" \
        docker compose -f "$release_dir/deploy/compose.yaml" "$@"
}

verify_container() {
    local status
    for _ in {1..30}; do
        status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' hermes-agent-bridge 2>/dev/null || true)"
        [[ "$status" == healthy ]] && return 0
        [[ "$status" == unhealthy ]] && return 1
        sleep 2
    done
    return 1
}

install_docker() {
    local release_dir="$1" previous="$2" network previous_version
    [[ "$(cfg_get BIND_HOST)" == 0.0.0.0 || "$(cfg_get BIND_HOST)" == "::" ]] || \
        die "Docker mode requires BIND_HOST=0.0.0.0 or BIND_HOST=:: for network reachability"
    command -v docker >/dev/null 2>&1 || install_docker_engine
    docker compose version >/dev/null 2>&1 || \
        die "Docker Compose v2 is required (docker compose)"
    network="$(choose_docker_network)"
    log "Pulling immutable container image $IMAGE_REPOSITORY:$VERSION"
    docker pull "$IMAGE_REPOSITORY:$VERSION"
    docker_compose "$release_dir" "$network" run --rm --no-deps \
        hermes-agent-bridge python -m hermes_agent_bridge check
    switch_current "$release_dir"
    if ! docker_compose "$release_dir" "$network" up -d --pull always --remove-orphans; then
        restore_config
        if [[ -n "$previous" ]]; then
            VERSION="$(basename "$previous")"
            switch_current "$previous"
            docker_compose "$previous" "$network" up -d --pull always --remove-orphans || true
        else
            docker_compose "$release_dir" "$network" down || true
        fi
        die "Docker Compose upgrade failed"
    fi
    if ! verify_container; then
        log "Health verification failed; rolling back"
        restore_config
        if [[ -n "$previous" ]]; then
            previous_version="$(basename "$previous")"
            VERSION="$previous_version"
            switch_current "$previous"
            docker_compose "$previous" "$network" up -d --pull always --remove-orphans
        else
            docker_compose "$release_dir" "$network" down
        fi
        die "upgrade rolled back because the container did not become healthy"
    fi
    printf 'MODE=docker\nVERSION=%s\nNETWORK=%s\nENV_FILE=%s\n' \
        "$VERSION" "$network" "$ENV_FILE" > "$INSTALL_ROOT/install-state"
    printf '\nCloudflare Tunnel origin: http://hermes-agent-bridge:%s\n' "$(cfg_get BIND_PORT)"
}

install_private_python() {
    local release_dir="$1" tools uv_installer
    tools="$release_dir/uv-bin"
    local runtime_python
    local -a candidates wheels
    install -d -m 0755 "$tools"
    uv_installer="$TEMP_DIR/uv-installer.sh"
    download "https://astral.sh/uv/${UV_VERSION}/install.sh" "$uv_installer"
    UV_UNMANAGED_INSTALL="$tools" sh "$uv_installer"
    "$tools/uv" python install "$PYTHON_VERSION" \
        --install-dir "$release_dir/runtime" --no-config
    mapfile -t candidates < <(find "$release_dir/runtime" -path '*/bin/python3' -type f -o -path '*/bin/python3' -type l)
    [[ ${#candidates[@]} -gt 0 ]] || die "private Python installation did not produce python3"
    runtime_python="${candidates[0]}"
    "$tools/uv" venv --python "$runtime_python" --seed "$release_dir/venv"
    "$tools/uv" pip install --python "$release_dir/venv/bin/python" \
        --require-hashes -r "$release_dir/requirements-runtime.txt"
    mapfile -t wheels < <(find "$release_dir" -maxdepth 1 -name 'hermes_agent_bridge-*.whl' -type f)
    [[ ${#wheels[@]} -eq 1 ]] || die "release bundle must contain exactly one wheel"
    "$tools/uv" pip install --python "$release_dir/venv/bin/python" \
        --no-deps "${wheels[0]}"
    rm -rf -- "$tools"
}

native_prerequisites() {
    local os_id arch
    [[ -d /run/systemd/system ]] || die "native mode requires a systemd-based host"
    # shellcheck source=/dev/null
    source /etc/os-release
    os_id="${ID:-}"
    [[ "$os_id" == debian || "$os_id" == ubuntu ]] || \
        die "native mode supports Debian and Ubuntu only"
    arch="$(uname -m)"
    [[ "$arch" == x86_64 || "$arch" == aarch64 ]] || \
        die "native mode supports amd64 and arm64 only"
    apt-get update
    apt-get install -y ca-certificates curl
}

verify_native() {
    local port="$1" bind_host target
    bind_host="$(cfg_get BIND_HOST)"
    if [[ "$bind_host" == "::" || "$bind_host" == "::1" ]]; then
        target="[::1]"
    else
        target="127.0.0.1"
    fi
    for _ in {1..30}; do
        if curl --silent --show-error --fail --max-time 2 --noproxy '*' \
            "http://${target}:${port}/healthz" >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    return 1
}

native_origin() {
    local bind_host port address
    bind_host="$(cfg_get BIND_HOST)"
    port="$(cfg_get BIND_PORT)"
    if [[ "$bind_host" == 0.0.0.0 ]]; then
        address="$(hostname -I 2>/dev/null | awk '{print $1}')"
        [[ -n "$address" ]] || address="127.0.0.1"
    elif [[ "$bind_host" == "::" ]]; then
        address="[::1]"
    else
        address="$bind_host"
    fi
    printf 'http://%s:%s' "$address" "$port"
}

restore_config() {
    if [[ -n "$CONFIG_BACKUP" && -f "$CONFIG_BACKUP" ]]; then
        install -o root -g root -m 0600 "$CONFIG_BACKUP" "$ENV_FILE"
    fi
}

install_native() {
    local release_dir="$1" previous="$2" unit_backup=""
    case "$(cfg_get BIND_HOST)" in
        0.0.0.0 | 127.0.0.1 | "::" | "::1") ;;
        *) die "native mode requires a wildcard or loopback BIND_HOST so protected /healthz verification remains local" ;;
    esac
    native_prerequisites
    if [[ ! -x "$release_dir/venv/bin/python" ]]; then
        log "Provisioning private Python $PYTHON_VERSION and locked dependencies"
        install_private_python "$release_dir"
    fi
    "$release_dir/venv/bin/python" -m hermes_agent_bridge check --env-file "$ENV_FILE"
    if ! id hermes-agent-bridge >/dev/null 2>&1; then
        useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin \
            --no-create-home hermes-agent-bridge
    fi
    chown -R root:root "$INSTALL_ROOT"
    chmod -R go-w "$INSTALL_ROOT"
    if [[ -f /etc/systemd/system/hermes-agent-bridge.service ]]; then
        unit_backup="$TEMP_DIR/hermes-agent-bridge.service.backup"
        cp /etc/systemd/system/hermes-agent-bridge.service "$unit_backup"
    fi
    sed "s|@ENV_FILE@|$ENV_FILE|g" \
        "$release_dir/deploy/hermes-agent-bridge.service" \
        > "$TEMP_DIR/hermes-agent-bridge.service"
    install -o root -g root -m 0644 "$TEMP_DIR/hermes-agent-bridge.service" \
        /etc/systemd/system/hermes-agent-bridge.service
    switch_current "$release_dir"
    systemctl daemon-reload
    systemctl enable hermes-agent-bridge.service
    if ! systemctl restart hermes-agent-bridge.service || ! verify_native "$(cfg_get BIND_PORT)"; then
        log "Health verification failed; rolling back"
        restore_config
        if [[ -n "$previous" ]]; then
            switch_current "$previous"
            if [[ -n "$unit_backup" ]]; then
                install -o root -g root -m 0644 "$unit_backup" \
                    /etc/systemd/system/hermes-agent-bridge.service
                systemctl daemon-reload
            fi
            systemctl restart hermes-agent-bridge.service || true
        else
            systemctl disable --now hermes-agent-bridge.service || true
        fi
        die "upgrade rolled back because the native service did not become healthy"
    fi
    printf 'MODE=native\nVERSION=%s\nENV_FILE=%s\n' \
        "$VERSION" "$ENV_FILE" > "$INSTALL_ROOT/install-state"
    printf '\nCloudflare Tunnel origin: %s\n' "$(native_origin)"
}

proxmox_version_check() {
    local major
    command -v pct >/dev/null 2>&1 || die "proxmox mode must run on a Proxmox VE host"
    major="$(pveversion | sed -n 's/.*pve-manager\/\([0-9][0-9]*\).*/\1/p')"
    [[ "$major" == 8 || "$major" == 9 ]] || \
        die "Proxmox VE 8 or 9 is required (found: $(pveversion))"
}

next_ctid() {
    if command -v pvesh >/dev/null 2>&1; then
        pvesh get /cluster/nextid
        return
    fi
    local id
    for id in $(seq 100 999999); do
        if ! pct status "$id" >/dev/null 2>&1; then
            printf '%s\n' "$id"
            return
        fi
    done
    die "could not find a free container ID"
}

create_debian_lxc() {
    local ctid="$1" network="$2" gateway="$3" script
    log "Creating Debian LXC $ctid with the Proxmox community script"
    script="$(curl --fail --silent --show-error --location "$DEBIAN_LXC_SCRIPT")"
    [[ -n "$script" ]] || die "the Proxmox Debian community script was empty"
    CREATED_CT="$ctid"
    if [[ "$network" == dhcp ]]; then
        mode=generated var_ctid="$ctid" bash -c "$script"
    else
        mode=generated var_ctid="$ctid" var_net="$network" \
            var_gateway="$gateway" bash -c "$script"
    fi
    pct status "$ctid" >/dev/null 2>&1 || \
        die "the community script did not create LXC $ctid"
}

wait_container_network() {
    local ctid="$1"
    for _ in {1..30}; do
        if pct exec "$ctid" -- getent hosts github.com >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    return 1
}

destroy_failed_container() {
    local ctid="$1"
    pct stop "$ctid" --skiplock 1 >/dev/null 2>&1 || true
    pct destroy "$ctid" --purge 1 >/dev/null 2>&1 || true
}

install_proxmox() {
    local ctid="" lxc_env original_env="" network="${var_net:-dhcp}"
    local gateway="${var_gateway:-}" static_answer="" requested=""
    local host_env="$ENV_FILE"
    proxmox_version_check
    if [[ -f "$PROXMOX_STATE" ]]; then
        # shellcheck source=/dev/null
        source "$PROXMOX_STATE"
        ctid="${CTID:-}"
        [[ -n "$ctid" ]] || die "$PROXMOX_STATE does not contain CTID"
        pct status "$ctid" >/dev/null 2>&1 || \
            die "recorded LXC $ctid no longer exists"
        lxc_env="$TEMP_DIR/container.env"
        pct pull "$ctid" "$DEFAULT_ENV_FILE" "$lxc_env"
        chmod 0600 "$lxc_env"
        original_env="$TEMP_DIR/original-container.env"
        cp "$lxc_env" "$original_env"
        ENV_FILE="$lxc_env"
        if [[ -f "$host_env" ]]; then
            configuration_wizard "$lxc_env" "$host_env"
        else
            configuration_wizard "$lxc_env"
        fi
        write_config "$lxc_env"
        log "Upgrading existing Hermes bridge LXC $ctid"
    else
        ctid="${var_ctid:-$(next_ctid)}"
        if [[ $NON_INTERACTIVE -eq 0 ]]; then
            read -r -p "Container ID [$ctid]: " requested
            [[ -z "$requested" ]] || ctid="$requested"
            if [[ "$network" == dhcp ]]; then
                read -r -p "Use a static IPv4 address instead of DHCP? [y/N] " static_answer
                if [[ "$static_answer" =~ ^[Yy]$ ]]; then
                    read -r -p "Static IPv4/CIDR (for example 192.168.1.20/24): " network
                    read -r -p "IPv4 gateway (for example 192.168.1.1): " gateway
                fi
            else
                read -r -p "Static IPv4/CIDR [$network]: " requested
                [[ -z "$requested" ]] || network="$requested"
                read -r -p "IPv4 gateway [$gateway]: " requested
                [[ -z "$requested" ]] || gateway="$requested"
            fi
        fi
        [[ "$ctid" =~ ^[1-9][0-9]*$ ]] || die "container ID must be a positive integer"
        pct status "$ctid" >/dev/null 2>&1 && die "container ID $ctid is already in use"
        if [[ "$network" != dhcp ]]; then
            [[ "$network" == */* ]] || die "static network must be an IPv4 address with CIDR prefix"
            [[ -n "$gateway" ]] || die "a gateway is required with a static network"
        else
            gateway=""
        fi
        create_debian_lxc "$ctid" "$network" "$gateway"
        lxc_env="$TEMP_DIR/container.env"
        ENV_FILE="$lxc_env"
        if [[ -f "$host_env" ]]; then
            configuration_wizard "$host_env"
        else
            configuration_wizard "$lxc_env"
        fi
        write_config "$lxc_env"
    fi
    pct start "$ctid" >/dev/null 2>&1 || true
    if ! wait_container_network "$ctid"; then
        if [[ -n "$CREATED_CT" ]]; then
            destroy_failed_container "$ctid"
            CREATED_CT=""
        fi
        die "LXC $ctid did not gain working network access"
    fi
    pct push "$ctid" "$ENV_FILE" "$DEFAULT_ENV_FILE" --perms 0600
    pct push "$ctid" "$SCRIPT_PATH" /root/hermes-agent-bridge-install.sh --perms 0755
    if ! pct exec "$ctid" -- /root/hermes-agent-bridge-install.sh native \
        --version "$VERSION" --env-file "$DEFAULT_ENV_FILE" --non-interactive; then
        if [[ -n "$CREATED_CT" ]]; then
            destroy_failed_container "$ctid"
            CREATED_CT=""
        elif [[ -n "$original_env" ]]; then
            pct push "$ctid" "$original_env" "$DEFAULT_ENV_FILE" --perms 0600
            pct exec "$ctid" -- systemctl restart hermes-agent-bridge.service || true
        fi
        die "native bootstrap failed inside LXC $ctid"
    fi
    rm -f -- "$ENV_FILE"
    ENV_FILE="$DEFAULT_ENV_FILE"
    printf 'CTID=%s\n' "$ctid" > "$PROXMOX_STATE"
    chmod 0600 "$PROXMOX_STATE"
    CREATED_CT=""
    local address
    address="$(pct exec "$ctid" -- hostname -I | awk '{print $1}')"
    printf '\nLXC address: %s\n' "$address"
    printf 'Cloudflare Tunnel origin: http://%s:%s\n' "$address" "$(cfg_get BIND_PORT)"
}

print_handoff() {
    local hostname aud
    hostname="$(cfg_get PUBLIC_HOSTNAMES)"
    hostname="${hostname%%,*}"
    aud="$(cfg_get CF_ACCESS_AUD)"
    cat <<EOF

Cloudflare Access handoff
  1. Route the tunnel hostname to the Cloudflare Tunnel origin printed above.
  2. Create an Access application for https://${hostname} using AUD ${aud}.
  3. Add separate Service Auth and operator Allow policies.
  4. Give the MCP client CF-Access-Client-Id and CF-Access-Client-Secret headers.

MCP URL: https://${hostname}/mcp

Troubleshooting
  Configuration/upstreams: /opt/hermes-agent-bridge/current/venv/bin/python -m hermes_agent_bridge check --env-file ${DEFAULT_ENV_FILE}
  Docker preflight: docker exec hermes-agent-bridge python -m hermes_agent_bridge check
  Native logs: journalctl -u hermes-agent-bridge -n 100 --no-pager
  Docker logs: docker logs hermes-agent-bridge
  Proxmox logs: pct exec <CTID> -- journalctl -u hermes-agent-bridge -n 100 --no-pager
EOF
}

main() {
    local source_path
    parse_args "$@"
    TEMP_DIR="$(mktemp -d /tmp/hermes-agent-bridge.XXXXXX)"
    chmod 0700 "$TEMP_DIR"
    source_path="${BASH_SOURCE[0]:-}"
    if [[ -n "$source_path" && -f "$source_path" ]]; then
        SCRIPT_PATH="$(readlink -f "$source_path")"
    else
        SCRIPT_PATH="$TEMP_DIR/hermes-agent-bridge-install.sh"
        download \
            "https://raw.githubusercontent.com/${RELEASE_REPOSITORY}/refs/heads/main/install.sh" \
            "$SCRIPT_PATH"
        chmod 0755 "$SCRIPT_PATH"
    fi
    SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"
    readonly SCRIPT_PATH SCRIPT_DIR

    if [[ "$MODE" == proxmox ]]; then
        install_proxmox
        print_handoff
        return
    fi

    if [[ -f "$ENV_FILE" ]]; then
        CONFIG_BACKUP="$TEMP_DIR/config.backup"
        cp "$ENV_FILE" "$CONFIG_BACKUP"
        chmod 0600 "$CONFIG_BACKUP"
    else
        CONFIG_WAS_NEW=1
    fi
    configuration_wizard "$ENV_FILE"
    write_config "$ENV_FILE"
    RESTORE_ON_EXIT=1

    local release_dir previous
    release_dir="$(prepare_release)"
    previous="$(existing_release || true)"
    case "$MODE" in
        docker) install_docker "$release_dir" "$previous" ;;
        native) install_native "$release_dir" "$previous" ;;
    esac
    CONFIG_BACKUP=""
    RESTORE_ON_EXIT=0
    print_handoff
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
