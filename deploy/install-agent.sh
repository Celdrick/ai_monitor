#!/usr/bin/env bash
# 在 GPU/NPU 机器上安装 ai-monitor-agent。
#
# 用法：
#   ./install-agent.sh --mode docker  --server http://monitor:8000 --token XXXX [--vendor nvidia|ascend] [--image ai-monitor-agent:latest]
#   ./install-agent.sh --mode systemd --server http://monitor:8000 --token XXXX [--src /path/to/repo/agent]
#
# 两种模式都会写入 /etc/ai-monitor/agent.yaml（已存在则不覆盖）。
set -euo pipefail

MODE=""
SERVER_URL=""
TOKEN=""
VENDOR=""
IMAGE="ai-monitor-agent:latest"
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)/agent"
CONFIG_DIR=/etc/ai-monitor
CONFIG_FILE="$CONFIG_DIR/agent.yaml"
LOKI_URL=""

usage() { sed -n '2,10p' "$0"; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --server) SERVER_URL="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --vendor) VENDOR="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --src) SRC_DIR="$2"; shift 2 ;;
    --loki) LOKI_URL="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown arg: $1"; usage ;;
  esac
done

[[ -z "$MODE" || -z "$SERVER_URL" || -z "$TOKEN" ]] && usage

detect_vendor() {
  if command -v nvidia-smi >/dev/null 2>&1; then echo nvidia; return; fi
  if command -v npu-smi >/dev/null 2>&1; then echo ascend; return; fi
  echo none
}
[[ -z "$VENDOR" ]] && VENDOR="$(detect_vendor)"
echo "hardware vendor: $VENDOR"

write_config() {
  sudo mkdir -p "$CONFIG_DIR"
  if [[ -f "$CONFIG_FILE" ]]; then
    echo "config exists, keep: $CONFIG_FILE"
    return
  fi
  sudo tee "$CONFIG_FILE" >/dev/null <<EOF
server_url: ${SERVER_URL}
agent_token: ${TOKEN}
loki_url: ${LOKI_URL:-null}
listen: 0.0.0.0:9400
scrape_interval_seconds: 15
heartbeat_interval_seconds: 30
services: []
artifacts_dir: /var/lib/ai-monitor-agent/artifacts
EOF
  sudo chmod 600 "$CONFIG_FILE"
  echo "wrote $CONFIG_FILE"
}

install_docker() {
  command -v docker >/dev/null 2>&1 || { echo "docker not found"; exit 1; }
  if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "building image $IMAGE from $SRC_DIR"
    docker build -t "$IMAGE" "$SRC_DIR"
  fi
  local dev_args=()
  case "$VENDOR" in
    nvidia) dev_args+=(--gpus all) ;;
    ascend)
      for d in /dev/davinci*; do [[ -e "$d" ]] && dev_args+=(--device "$d"); done
      for d in /dev/davinci_manager /dev/devmm_svm /dev/hisi_hdc; do [[ -e "$d" ]] && dev_args+=(--device "$d"); done
      dev_args+=(-v /usr/local/Ascend/driver:/usr/local/Ascend/driver:ro)
      [[ -e /usr/local/bin/npu-smi ]] && dev_args+=(-v /usr/local/bin/npu-smi:/usr/local/bin/npu-smi:ro)
      dev_args+=(-e LD_LIBRARY_PATH=/usr/local/Ascend/driver/lib64:/usr/local/Ascend/driver/lib64/driver)
      ;;
  esac
  docker rm -f ai-monitor-agent >/dev/null 2>&1 || true
  docker run -d --name ai-monitor-agent --restart=always \
    --net=host --pid=host \
    -v "$CONFIG_DIR":"$CONFIG_DIR":ro \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /var/lib/ai-monitor-agent:/var/lib/ai-monitor-agent \
    ${dev_args[@]+"${dev_args[@]}"} \
    "$IMAGE"
  echo "started container ai-monitor-agent (metrics on :9400)"
}

install_systemd() {
  command -v python3 >/dev/null 2>&1 || { echo "python3 not found"; exit 1; }
  local prefix=/opt/ai-monitor-agent
  sudo mkdir -p "$prefix" /var/lib/ai-monitor-agent
  sudo python3 -m venv "$prefix/.venv"
  sudo "$prefix/.venv/bin/pip" install --upgrade pip >/dev/null
  sudo "$prefix/.venv/bin/pip" install "$SRC_DIR"
  sudo cp "$(dirname "$0")/agent/ai-monitor-agent.service" /etc/systemd/system/ai-monitor-agent.service
  sudo systemctl daemon-reload
  sudo systemctl enable --now ai-monitor-agent
  sudo systemctl --no-pager status ai-monitor-agent | head -5
}

write_config
case "$MODE" in
  docker) install_docker ;;
  systemd) install_systemd ;;
  *) echo "mode must be docker or systemd"; exit 1 ;;
esac
