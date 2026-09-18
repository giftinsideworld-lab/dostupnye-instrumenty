#!/usr/bin/env bash
#
# развернуть-агента.sh — разворачивает нового Агента и Telegram-бота на чистый сервер.
#
# Запускается НА КОМПЬЮТЕРЕ, а не на сервере. Код бота берётся из локальной копии
# приватного репозитория — поэтому репозиторий можно не открывать наружу.
#
# Использование:
#   ./развернуть-агента.sh --ip 1.2.3.4 --ключ ~/путь/к/ключу --токен 123:ABC
#
# Дополнительно:
#   --код ~/путь/к/agent-bot   папка с кодом бота (по умолчанию ищется рядом)
#   --днк ~/путь/к/папке       папка с DNA-файлами Агента, если переносим существующего
#
set -uo pipefail

IP=""; KEY=""; TOKEN=""; CODE=""; DNA=""

while [ $# -gt 0 ]; do
  case "$1" in
    --ip)     IP="$2"; shift 2 ;;
    --ключ)   KEY="$2"; shift 2 ;;
    --токен)  TOKEN="$2"; shift 2 ;;
    --код)    CODE="$2"; shift 2 ;;
    --днк)    DNA="$2"; shift 2 ;;
    *) echo "Неизвестный параметр: $1"; exit 1 ;;
  esac
done

fail() { echo "✖ $*" >&2; exit 1; }
step() { echo; echo "▸ $*"; }

[ -n "$IP" ]    || fail "Не указан --ip: адрес сервера"
[ -n "$KEY" ]   || fail "Не указан --ключ: файл SSH-ключа"
[ -n "$TOKEN" ] || fail "Не указан --токен: токен бота от @BotFather"
[ -f "$KEY" ]   || fail "Файл ключа не найден: $KEY"

# Ищем код бота: явно указанный путь, либо рядом со скриптом
if [ -z "$CODE" ]; then
  HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  for candidate in "$HERE/../../agent-bot" "$HOME/agent-bot" "$HOME/Documents/agent-bot"; do
    [ -f "$candidate/bot/index.js" ] && CODE="$candidate" && break
  done
fi
[ -n "$CODE" ] && [ -f "$CODE/bot/index.js" ] || fail "Не нашёл код бота. Укажите путь: --код ~/путь/к/agent-bot"

SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o BatchMode=yes root@$IP"

step "Проверяю связь с сервером $IP"
$SSH "echo ok" >/dev/null 2>&1 || fail "Не подключился. Проверьте адрес, ключ и что сервер запущен"
echo "  связь есть: $($SSH '. /etc/os-release && echo $PRETTY_NAME')"

step "Ставлю базовые пакеты"
$SSH 'apt-get update -qq >/dev/null 2>&1; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl git jq unzip rsync tmux >/dev/null 2>&1; echo ok' >/dev/null
echo "  готово"

step "Node.js"
$SSH 'command -v node >/dev/null 2>&1 || { curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null 2>&1; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs >/dev/null 2>&1; }; node -v' \
  || fail "Node.js не установился"

step "Claude Code CLI"
$SSH 'command -v claude >/dev/null 2>&1 || npm install -g @anthropic-ai/claude-code >/dev/null 2>&1; claude --version' \
  || fail "Claude Code CLI не установился"

step "VS Code CLI — для туннеля"
$SSH 'command -v code >/dev/null 2>&1 || { curl -fsSL "https://code.visualstudio.com/sha/download?build=stable&os=cli-alpine-x64" -o /tmp/vscode.tar.gz && tar -xzf /tmp/vscode.tar.gz -C /usr/local/bin/ && rm -f /tmp/vscode.tar.gz; }; code --version 2>/dev/null | head -1' \
  || echo "  не установился — туннель будет недоступен, остальное работает"

step "Пользователь agent и рабочие папки"
$SSH 'id agent >/dev/null 2>&1 || useradd -m -s /bin/bash agent
mkdir -p /home/agent/workspace/memory /home/agent/workspace/knowledge /home/agent/projects /home/agent/.agent/bot /home/agent/.claude/skills
CR=$(readlink -f $(command -v claude)); chmod -R a+rX "$(dirname "$CR")" 2>/dev/null; chmod -R a+rX "$(dirname "$(dirname "$CR")")" 2>/dev/null
sysctl -w net.ipv6.conf.all.disable_ipv6=1 >/dev/null 2>&1
sysctl -w net.ipv6.conf.default.disable_ipv6=1 >/dev/null 2>&1
chown -R agent:agent /home/agent; echo ok' >/dev/null
echo "  создан, IPv6 отключён"

step "Переношу код бота с компьютера"
tar czf - --exclude node_modules --exclude .git -C "$CODE/bot" . \
  | $SSH 'tar xzf - -C /home/agent/.agent/bot' || fail "Не скопировался код бота"
$SSH 'cd /home/agent/.agent/bot && npm install --omit=dev >/dev/null 2>&1 && chmod +x update-bot.sh 2>/dev/null; echo ok' >/dev/null
echo "  код на месте, зависимости установлены"

if [ -n "$DNA" ] && [ -d "$DNA" ]; then
  step "Переношу файлы Агента"
  ( cd "$DNA" && tar czf - $(ls *.md 2>/dev/null) memory knowledge 2>/dev/null ) \
    | $SSH 'tar xzf - -C /home/agent/workspace' && echo "  файлы Агента перенесены"
  if [ -d "$DNA/.claude" ]; then
    tar czf - --exclude '.cc-writes' -C "$DNA/.claude" . | $SSH 'tar xzf - -C /home/agent/.claude'
    echo "  настройки и скиллы перенесены"
  fi
fi

step "Токен бота и системный сервис"
printf 'BOT_TOKEN=%s\nAGENT_HOME=/home/agent\n' "$TOKEN" \
  | $SSH 'cat > /home/agent/.agent/.env && chmod 600 /home/agent/.agent/.env'
$SSH 'ln -sf /home/agent/workspace/CLAUDE.md /home/agent/CLAUDE.md 2>/dev/null
chown -h agent:agent /home/agent/CLAUDE.md 2>/dev/null
chown -R agent:agent /home/agent
cp /home/agent/.agent/bot/agent-bot.service /etc/systemd/system/agent-bot.service
systemctl daemon-reload && systemctl enable agent-bot >/dev/null 2>&1; echo ok' >/dev/null
echo "  токен записан, автозапуск включён"

cat <<FINAL

═══════════════════════════════════════════════════════════════════
  Сервер готов. Остался один шаг — авторизация Claude.
═══════════════════════════════════════════════════════════════════

Программно её сделать нельзя: интерфейс Claude принимает только живое
нажатие клавиши. Надёжный способ — через tmux, он создаёт настоящий
терминал. Выполните по очереди:

  ssh -i "$KEY" root@$IP

  sudo -u agent -H tmux new-session -d -s login -x 200 -y 50 claude
  sleep 12
  sudo -u agent -H tmux send-keys -t login Enter
  sleep 6
  sudo -u agent -H tmux send-keys -t login Enter
  sleep 6
  sudo -u agent -H tmux capture-pane -t login -p | tail -20

Появится ссылка https://claude.com/cai/oauth/... — откройте её,
нажмите Authorize, скопируйте код и отправьте:

  sudo -u agent -H tmux send-keys -t login -l "ВСТАВЬТЕ_КОД"
  sudo -u agent -H tmux send-keys -t login Enter
  sleep 15
  sudo -u agent -H tmux capture-pane -t login -p | tail -5

Увидели «Login successful» — запускайте:

  sudo -u agent -H tmux kill-server
  systemctl start agent-bot
  systemctl status agent-bot --no-pager

Готово. Напишите боту /start ПЕРВОЙ — он привяжется к вам
и остальных будет игнорировать.

FINAL
