#!/usr/bin/env bash
# Make `sinobee` available in every terminal without `cd` or `source .venv/bin/activate`.
# Run once from the repo:  bash scripts/install-command.sh
# It links .venv/bin/sinobee into ~/.local/bin and adds that folder to PATH if needed.
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
venv="$repo/.venv"
bin="$HOME/.local/bin"

if [ ! -x "$venv/bin/python" ]; then
  echo "建立虛擬環境 $venv"
  python3 -m venv "$venv"
fi
echo "安裝／更新 SinoBee 與相依套件（可編輯模式，之後 git pull 程式即生效）"
"$venv/bin/pip" install -q -e "$repo[all]"

if "$venv/bin/python" -c "import playwright" 2>/dev/null; then
  "$venv/bin/python" -m playwright install chromium >/dev/null 2>&1 || echo "（瀏覽器元件稍後可用 sinobee doctor 檢查）"
fi

mkdir -p "$bin"
ln -sf "$venv/bin/sinobee" "$bin/sinobee"
echo "已連結 $bin/sinobee → $venv/bin/sinobee"

case ":$PATH:" in
  *":$bin:"*) ;;
  *)
    for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
      if [ -f "$rc" ] || [ "$rc" = "$HOME/.zshrc" ]; then
        if ! grep -qs 'HOME/.local/bin' "$rc"; then
          printf '\n# SinoBee\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
          echo "已把 ~/.local/bin 加入 PATH（$rc）"
        fi
      fi
    done
    echo "請開新的終端機視窗，或執行：export PATH=\"\$HOME/.local/bin:\$PATH\""
    ;;
esac

echo
echo "完成。之後在任何資料夾執行： sinobee serve"
