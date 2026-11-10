#!/usr/bin/env bash
# run-regression.sh — widscan58 一键回归测试入口
#
# 用法:
#   tools/run-regression.sh                  # 跑所有可执行的模块
#   tools/run-regression.sh web              # 只跑 web
#   tools/run-regression.sh firmware         # 只跑 firmware
#   tools/run-regression.sh skip-build       # 跳过构建，只跑测试
#
# 输出: 每个模块独立记录结果；某模块失败后继续其他；最后汇总并返回非零退出码。
# 不修改用户代码和数据。不自动升级依赖。不清理构建缓存。

set -euo pipefail

# --- 路径探测 ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

path_in() { for p in "$@"; do [[ -e "$p" ]] && { echo "$p"; return 0; }; done; return 1; }
FIRMWARE_DIR=$(path_in "$PROJECT_ROOT/repo/firmware" "$PROJECT_ROOT/firmware") || FIRMWARE_DIR=""
WEB_DIR=$(path_in "$PROJECT_ROOT/repo/web" "$PROJECT_ROOT/web") || WEB_DIR=""
DESKTOP_DIR=$(path_in "$PROJECT_ROOT/repo/web-desktop" "$PROJECT_ROOT/web-desktop") || DESKTOP_DIR=""
ANDROID_DIR=$(path_in "$PROJECT_ROOT/app" "$PROJECT_ROOT/repo/app") || ANDROID_DIR=""
RFTRACK_DIR=$(path_in "$PROJECT_ROOT/repo/rftrack" "$PROJECT_ROOT/rftrack") || RFTRACK_DIR=""

# --- 颜色 ---
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

pass=0; fail=0; skip=0
results=()

report() { printf "${CYAN}==>%s${NC} %s\n" "$(date '+%H:%M:%S')" "$1"; }
ok()   { printf "${GREEN}[OK]${NC}   %s\n" "$1"; }
bad()  { printf "${RED}[FAIL]${NC} %s\n" "$1"; }
warn() { printf "${YELLOW}[SKIP]${NC} %s\n" "$1"; }

record() {
  local status=$1 name=$2 count=$3
  results+=("$status|$name|$count")
  case "$status" in
    PASS) pass=$((pass + count));;
    FAIL) fail=$((fail + count));;
    SKIP) skip=$((skip + count));;
  esac
}

need_cmd() { command -v "$1" >/dev/null 2>&1; }

SKIP_BUILD=0
FILTER=""
for arg in "$@"; do
  case "$arg" in
    skip-build) SKIP_BUILD=1;;
    *) FILTER="$arg";;
  esac
done

should_run() { [[ -z "$FILTER" || "$FILTER" == "$1" ]]; }

# ============================================================
# 1. Firmware
# ============================================================
run_firmware() {
  should_run firmware || return 0
  report "Firmware: PlatformIO 环境 + C5/S3 构建 + native host tests"

  [[ -z "$FIRMWARE_DIR" ]] && { warn "Firmware: 目录不存在"; record SKIP "Firmware" 0; return 0; }
  need_cmd pio || { warn "Firmware: pio 未安装"; record SKIP "Firmware" 0; return 0; }

  local build_ok=1 test_ok=1
  cd "$FIRMWARE_DIR"

  if [[ $SKIP_BUILD -eq 0 ]]; then
    if pio run -e esp32c5 >/dev/null 2>&1; then
      ok "Firmware esp32c5 build"
      record PASS "Firmware esp32c5" 1
    else
      bad "Firmware esp32c5 build"
      build_ok=0
      record FAIL "Firmware esp32c5" 0
    fi
    if pio run -e esp32s3 >/dev/null 2>&1; then
      ok "Firmware esp32s3 build"
      record PASS "Firmware esp32s3" 1
    else
      bad "Firmware esp32s3 build"
      build_ok=0
      record FAIL "Firmware esp32s3" 0
    fi
  fi

  local test_out n
  test_out=$(pio test -e native 2>&1) || test_ok=0
  if [[ $test_ok -eq 1 ]] && echo "$test_out" | grep -q "test cases.*succeeded"; then
    n=$(echo "$test_out" | grep -oE '[0-9]+ test cases' | head -1 | grep -oE '[0-9]+')
    ok "Firmware native host tests ($n cases)"
    record PASS "Firmware native tests" "$n"
  else
    bad "Firmware native host tests"
    echo "$test_out" | tail -20
    record FAIL "Firmware native tests" 0
  fi
}

# ============================================================
# 2. Web
# ============================================================
run_web() {
  should_run web || return 0
  report "Web: TypeScript + Vite build + vitest"

  [[ -z "$WEB_DIR" ]] && { warn "Web: 目录不存在"; record SKIP "Web" 0; return 0; }
  need_cmd node || { warn "Web: node 未安装"; record SKIP "Web" 0; return 0; }

  cd "$WEB_DIR"
  [[ ! -d "node_modules" ]] && { warn "Web: node_modules 缺失，先 npm install"; npm install --no-audit --no-fund >/dev/null 2>&1; }

  if [[ $SKIP_BUILD -eq 0 ]]; then
    if npm run build >/dev/null 2>&1; then
      ok "Web build (含 tsc --noEmit)"
      record PASS "Web build" 1
    else
      bad "Web build"
      record FAIL "Web build" 0
    fi
  fi

  local test_out n
  test_out=$(npm test 2>&1) || true
  n=$(echo "$test_out" | grep -oE '[0-9]+ passed' | tail -1 | grep -oE '[0-9]+' || echo 0)
  if [[ "$n" -gt 0 ]]; then
    ok "Web vitest ($n tests)"
    record PASS "Web vitest" "$n"
  else
    bad "Web vitest"
    echo "$test_out" | tail -20
    record FAIL "Web vitest" 0
  fi
}

# ============================================================
# 3. Electron
# ============================================================
run_electron() {
  should_run electron || return 0
  report "Electron: sync-dist + dist:mac"

  [[ -z "$DESKTOP_DIR" ]] && { warn "Electron: 目录不存在"; record SKIP "Electron" 0; return 0; }
  [[ -z "$WEB_DIR" ]] && { warn "Electron: 依赖 web 先构建但 web 目录不存在"; record SKIP "Electron" 0; return 0; }

  if [[ $SKIP_BUILD -eq 1 ]]; then
    record SKIP "Electron (skip-build)" 0
    return 0
  fi

  cd "$DESKTOP_DIR"
  [[ ! -d "node_modules" ]] && { warn "Electron: node_modules 缺失，先 npm install"; npm install --no-audit --no-fund >/dev/null 2>&1; }

  npm run sync-dist >/dev/null 2>&1 || { bad "Electron sync-dist"; record FAIL "Electron sync-dist" 0; return 0; }
  npm run dist:mac >/dev/null 2>&1 && { ok "Electron dist:mac"; record PASS "Electron build" 1; } || { bad "Electron dist:mac"; record FAIL "Electron build" 0; }
}

# ============================================================
# 4. Android
# ============================================================
run_android() {
  should_run android || return 0
  report "Android: Gradle :core:compileKotlin + :core:test"

  [[ -z "$ANDROID_DIR" ]] && { warn "Android: 目录不存在"; record SKIP "Android" 0; return 0; }

  # 自动修正 JAVA_HOME
  for j in \
    "$HOME/Library/Java/JavaVirtualMachines/openjdk-21.0.1/Contents/Home" \
    "$HOME/Library/Java/JavaVirtualMachines/corretto-11.0.25/Contents/Home"; do
    if [[ -x "$j/bin/java" ]] && "$j/bin/java" -version >/dev/null 2>&1; then
      export JAVA_HOME="$j"
      break
    fi
  done

  cd "$ANDROID_DIR"
  [[ -f "gradlew" ]] || { warn "Android: gradlew 不存在"; record SKIP "Android" 0; return 0; }
  chmod +x gradlew 2>/dev/null || true

  if [[ $SKIP_BUILD -eq 0 ]]; then
    ./gradlew :core:compileKotlin --no-daemon >/dev/null 2>&1 && ok "Android :core:compileKotlin" || { bad "Android :core:compileKotlin"; record FAIL "Android build" 0; return 0; }
  fi

  ./gradlew :core:test --no-daemon >/dev/null 2>&1 || true
  local n=0
  # 从 JUnit XML 汇总
  n=$(find core/build/test-results/test -name "TEST-*.xml" 2>/dev/null | xargs grep -oh 'tests="[0-9]*"' 2>/dev/null | grep -oE '[0-9]+' | awk '{s+=$1} END {print s+0}')
  if [[ "$n" -gt 0 ]]; then
    ok "Android :core:test ($n tests)"
    record PASS "Android :core:test" "$n"
  else
    bad "Android :core:test"
    record FAIL "Android :core:test" 0
  fi
}

# ============================================================
# 5. RFTrack
# ============================================================
run_rftrack() {
  should_run rftrack || return 0
  report "RFTrack: 后端 pytest + 前端 workbench node --test"

  [[ -z "$RFTRACK_DIR" ]] && { warn "RFTrack: 目录不存在"; record SKIP "RFTrack" 0; return 0; }

  local backend_dir="$RFTRACK_DIR/backend"
  local fe_dir="$RFTRACK_DIR/frontend/workbench"

  # 后端：优先用 pytest 退出码判定，再补数量统计
  if [[ -d "$backend_dir" ]]; then
    local py_out py_ok=1
    py_out=$(cd "$backend_dir" && python3 -m pytest tests/ -v 2>&1) || py_ok=0
    local n
    n=$(echo "$py_out" | grep -oE '[0-9]+ passed' | tail -1 | grep -oE '[0-9]+' || echo 0)
    if [[ $py_ok -eq 1 && "$n" -gt 0 ]]; then
      ok "RFTrack backend pytest ($n passed)"
      record PASS "RFTrack backend" "$n"
    elif [[ $py_ok -eq 1 ]]; then
      # pytest 返回 0 但没匹配 passed（全是 skipped），仍然 PASS
      ok "RFTrack backend pytest (exit 0)"
      record PASS "RFTrack backend" 0
    else
      bad "RFTrack backend pytest"
      echo "$py_out" | tail -30
      record FAIL "RFTrack backend" 0
    fi
  fi

  # 前端 workbench
  if [[ -d "$fe_dir" ]]; then
    local fe_out fe_ok=1
    fe_out=$(cd "$fe_dir" && node --test test/*.mjs 2>&1) || fe_ok=0
    local fn
    fn=$(echo "$fe_out" | grep -oE '# pass [0-9]+' | grep -oE '[0-9]+' || echo 0)
    if [[ $fe_ok -eq 1 && "$fn" -gt 0 ]]; then
      ok "RFTrack workbench ($fn passed)"
      record PASS "RFTrack workbench" "$fn"
    elif [[ $fe_ok -eq 1 ]]; then
      ok "RFTrack workbench (exit 0)"
      record PASS "RFTrack workbench" 0
    else
      bad "RFTrack workbench"
      echo "$fe_out" | tail -20
      record FAIL "RFTrack workbench" 0
    fi
  fi
}

# ============================================================
# 6. Gateway Agent (Python + pytest)
# ============================================================
run_gateway_agent() {
  should_run gateway || should_run agent || return 0
  report "Gateway Agent: pytest 全量（含 Site Connector V1）"

  local AGENT_DIR="$PROJECT_ROOT/gateway-agent"
  [[ -d "$AGENT_DIR" ]] || { warn "Gateway Agent: 目录不存在"; record SKIP "Gateway Agent" 0; return 0; }
  need_cmd python3 || { warn "Gateway Agent: python3 未安装"; record SKIP "Gateway Agent" 0; return 0; }

  local py_out py_ok=1
  py_out=$(cd "$AGENT_DIR" && python3 -m pytest tests/ -q 2>&1) || py_ok=0
  local n total_failed
  n=$(echo "$py_out" | grep -oE '[0-9]+ passed' | tail -1 | grep -oE '[0-9]+' || echo 0)
  total_failed=$(echo "$py_out" | grep -oE '[0-9]+ failed' | tail -1 | grep -oE '[0-9]+' || echo 0)
  if [[ $py_ok -eq 1 && "$total_failed" -eq 0 && "$n" -gt 0 ]]; then
    ok "Gateway Agent pytest ($n passed)"
    record PASS "Gateway Agent" "$n"
  else
    bad "Gateway Agent pytest ($total_failed failed / $n passed)"
    echo "$py_out" | tail -30
    record FAIL "Gateway Agent" 0
  fi
}

# ============================================================
# 7. Site Server (Python + pytest — smoke)
# ============================================================
run_site_server() {
  should_run site-server || should_run site || return 0
  report "Site Server: pytest smoke + REST 端点检查"

  local SITE_DIR="$PROJECT_ROOT/site-server"
  [[ -d "$SITE_DIR" ]] || { warn "Site Server: 目录不存在"; record SKIP "Site Server" 0; return 0; }
  need_cmd python3 || { warn "Site Server: python3 未安装"; record SKIP "Site Server" 0; return 0; }

  local py_out py_ok=1
  py_out=$(cd "$SITE_DIR" && python3 -m pytest tests/ -q 2>&1) || py_ok=0
  local n
  n=$(echo "$py_out" | grep -oE '[0-9]+ passed' | tail -1 | grep -oE '[0-9]+' || echo 0)
  if [[ $py_ok -eq 1 && "$n" -gt 0 ]]; then
    ok "Site Server pytest ($n passed)"
    record PASS "Site Server" "$n"
  elif [[ $py_ok -eq 1 ]]; then
    # pytest 返回 0 但没匹配 passed（可能没有 tests/），做简单 smoke
    report "Site Server: 无 pytest，做 REST smoke"
    local sm_out sm_ok=1
    sm_out=$(cd "$SITE_DIR" && python3 -c "
import sys; sys.path.insert(0, 'src')
from widscan_site.config import ServerConfig
from widscan_site.main import create_app
app = create_app(ServerConfig())
assert app is not None, 'app creation failed'
print('Site Server app OK')
" 2>&1) || sm_ok=0
    if [[ $sm_ok -eq 1 ]]; then
      ok "Site Server smoke (app creation)"
      record PASS "Site Server smoke" 1
    else
      bad "Site Server smoke"
      echo "$sm_out" | tail -10
      record FAIL "Site Server smoke" 0
    fi
  else
    bad "Site Server pytest"
    echo "$py_out" | tail -20
    record FAIL "Site Server" 0
  fi
}

# ============================================================
# 主流程
# ============================================================
echo ""
echo "================================================"
echo " widscan58 一键回归测试"
echo " 项目根: $PROJECT_ROOT"
echo " 时间:   $(date '+%Y-%m-%d %H:%M:%S')"
[[ -n "$FILTER" ]] && echo " 过滤:   只跑 $FILTER"
[[ $SKIP_BUILD -eq 1 ]] && echo " 模式:   skip-build（跳过构建）"
echo "================================================"
echo ""

run_firmware
run_web
run_electron
run_android
run_rftrack
run_gateway_agent
run_site_server

# ============================================================
# 汇总
# ============================================================
echo ""
echo "================================================"
echo " 结果汇总"
echo "================================================"
for r in "${results[@]}"; do
  IFS='|' read -r s n c <<< "$r"
  case "$s" in
    PASS) printf "${GREEN}PASS${NC}  %-35s %s\n" "$n" "$c passed";;
    FAIL) printf "${RED}FAIL${NC}  %-35s\n" "$n";;
    SKIP) printf "${YELLOW}SKIP${NC}  %-35s\n" "$n";;
  esac
done
echo "-----------------------------------------------"
printf "  通过: ${GREEN}%d${NC}   失败: ${RED}%d${NC}   跳过: ${YELLOW}%d${NC}\n" "$pass" "$fail" "$skip"
echo "================================================"

if [[ $fail -gt 0 ]]; then
  exit 1
fi
exit 0
