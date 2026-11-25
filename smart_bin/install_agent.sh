#!/bin/bash
# 수거함 대기 프로그램(bin_agent.py)을 라즈베리 파이가 켜질 때 자동으로 실행되게 등록한다.
# 한 번만 실행하면 된다. 이후에는 전원만 켜면 웹의 '수거 시작' 버튼을 기다린다.
#
#   bash ~/campus-recycle/smart_bin/install_agent.sh
#
# 상태 확인:  systemctl status bin-agent
# 로그 보기:  journalctl -u bin-agent -f
# 잠깐 끄기:  sudo systemctl stop bin-agent      (main.py를 손으로 시험할 때)
# 다시 켜기:  sudo systemctl start bin-agent
# 등록 해제:  sudo systemctl disable --now bin-agent
set -e

USER_NAME="$(whoami)"
AGENT="$HOME/campus-recycle/smart_bin/bin_agent.py"
MAIN="$HOME/smart_bin/main.py"
PYTHON="$HOME/smart_bin/.venv_ai/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

[ -f "$AGENT" ] || { echo "대기 프로그램이 없습니다: $AGENT (cd ~/campus-recycle && git pull origin main)"; exit 1; }
[ -f "$MAIN" ] || { echo "main.py가 없습니다: $MAIN"; exit 1; }

sudo tee /etc/systemd/system/bin-agent.service >/dev/null <<EOF
[Unit]
Description=Campus smart bin - wait for the web start button
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$HOME/smart_bin
Environment=PYTHONUNBUFFERED=1
ExecStart=$PYTHON $AGENT $MAIN
Restart=always
RestartSec=5
# 끌 때 Ctrl+C와 같은 신호를 보내서, 분류 중이었다면 서보를 원위치로 돌리고 끝나게 한다.
KillSignal=SIGINT
TimeoutStopSec=45

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable bin-agent.service
sudo systemctl restart bin-agent.service
sleep 4
systemctl --no-pager --lines=6 status bin-agent.service || true
echo
echo "등록 완료: 이제 파이를 켜면 자동으로 '수거 시작'을 기다립니다."
echo "로그 보기: journalctl -u bin-agent -f"
