#!/usr/bin/env bash
# 在 GPU/NPU 机器上安装 ai-monitor-agent。
#
# 用法：
#   ./install-agent.sh --mode docker  --server http://monitor:8000 --token XXXX [--vendor nvidia|ascend] [--image ai-monitor-agent:latest]
#   ./install-agent.sh --mode systemd --server http://monitor:8000 --token XXXX [--src /path/to/repo/agent]
# 通用可选参数：
#   --loki http://monitor:3100   日志推送地址（缺省：与 --server 同主机的 3100 端口）
#   --log-dir /var/log/vllm      vLLM 日志文件所在目录，可重复；Docker 模式会以只读方式挂载进容器
#
# 两种模式都会写入 /etc/ai-monitor/agent.yaml（已存在则不覆盖）。手动登记 nohup 启动的 vLLM 请编辑该文件的 services 段。
# Docker 模式会加 SYS_PTRACE 以便 py-spy 附着进程。nsys / msprof 需装在宿主机；systemd 模式更容易直接调用这两套工具。
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
LOG_DIRS=()

usage() { sed -n '2,13p' "$0"; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --server) SERVER_URL="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --vendor) VENDOR="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --src) SRC_DIR="$2"; shift 2 ;;
    --loki) LOKI_URL="$2"; shift 2 ;;
    --log-dir) LOG_DIRS+=("$2"); shift 2 ;;
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

# 缺省 Loki 地址：与 server 同主机，端口 3100
if [[ -z "$LOKI_URL" ]]; then
  server_host="${SERVER_URL#*://}"; server_host="${server_host%%/*}"; server_host="${server_host%%:*}"
  LOKI_URL="http://${server_host}:3100"
fi
echo "loki url: $LOKI_URL"

write_config() {
  sudo mkdir -p "$CONFIG_DIR"
  if [[ -f "$CONFIG_FILE" ]]; then
    echo "config exists, keep: $CONFIG_FILE"
    return
  fi
  sudo tee "$CONFIG_FILE" >/dev/null <<EOF
server_url: ${SERVER_URL}
agent_token: ${TOKEN}
loki_url: ${LOKI_URL}
listen: 0.0.0.0:9400
scrape_interval_seconds: 15
heartbeat_interval_seconds: 30
# 手动登记 nohup/tmux 启动的 vLLM（Docker 容器会自动发现，无需登记）：
# services:
#   - name: qwen-72b
#     port: 8001
#     log_path: /var/log/vllm/qwen-72b.log
#     profiler_dir: /data/vllm_profile/qwen-72b
services: []
discovery:
  docker: true
  process: true
  interval_seconds: 30
logs:
  enabled: true
  batch_lines: 500
  batch_interval_seconds: 1
  buffer_max_lines: 10000
state_dir: /var/lib/ai-monitor-agent/state
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
  for d in ${LOG_DIRS[@]+"${LOG_DIRS[@]}"}; do dev_args+=(-v "$d":"$d":ro); done
  docker rm -f ai-monitor-agent >/dev/null 2>&1 || true
  docker run -d --name ai-monitor-agent --restart=always \
    --net=host --pid=host \
    --cap-add=SYS_PTRACE --security-opt seccomp=unconfined \
    -v "$CONFIG_DIR":"$CONFIG_DIR":ro \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /var/lib/ai-monitor-agent:/var/lib/ai-monitor-agent \
    ${dev_args[@]+"${dev_args[@]}"} \
    "$IMAGE"
  echo "started container ai-monitor-agent (metrics on :9400; SYS_PTRACE for py-spy)"
  echo "note: install nsys / msprof on the host; systemd mode can invoke them more easily than Docker."
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
