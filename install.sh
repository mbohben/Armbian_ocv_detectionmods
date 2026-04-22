#!/bin/bash

set -e

echo "=============================="
echo " Printer AI Installer"
echo "=============================="

APP_DIR="$HOME/printer_ai"

echo "[1/8] Installing system packages..."
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg git

echo "[2/8] Creating virtual environment..."
cd $APP_DIR
python3 -m venv venv

echo "[3/8] Installing Python dependencies..."
source venv/bin/activate
pip install --upgrade pip
pip install opencv-python-headless numpy requests flask psutil

echo "[4/8] Creating folders..."
mkdir -p buffer events templates

echo "[5/8] Creating default config if missing..."
if [ ! -f config.json ]; then
cat <<EOF > config.json
{
  "mode": "monitor",

  "printer": {
    "type": "corexy_gantry",

    "kinematics": {
      "x_min": 0,
      "x_max": 235,
      "y_min": 0,
      "y_max": 250
    },

    "bed": {
      "x_min": 10,
      "x_max": 225,
      "y_min": 10,
      "y_max": 240
    }
  },

  "calibration": {
    "transform_matrix": [],
    "mask_radius": 40
  }
}
EOF
fi

echo "[6/8] Creating run scripts..."

cat <<EOF > run_ai.sh
#!/bin/bash
cd $APP_DIR
source venv/bin/activate
nice -n 10 python main.py
EOF

cat <<EOF > run_web.sh
#!/bin/bash
cd $APP_DIR
source venv/bin/activate
python webui.py
EOF

chmod +x run_ai.sh run_web.sh

echo "[7/8] Creating systemd services..."

sudo bash -c "cat > /etc/systemd/system/printer-ai.service <<EOL
[Unit]
Description=Printer AI
After=network.target

[Service]
ExecStart=$APP_DIR/run_ai.sh
Restart=always
User=$USER

[Install]
WantedBy=multi-user.target
EOL"

sudo bash -c "cat > /etc/systemd/system/printer-ai-ui.service <<EOL
[Unit]
Description=Printer AI Web UI
After=network.target

[Service]
ExecStart=$APP_DIR/run_web.sh
Restart=always
User=$USER

[Install]
WantedBy=multi-user.target
EOL"

echo "[8/8] Enabling services..."

sudo systemctl daemon-reexec
sudo systemctl daemon-reload
sudo systemctl enable printer-ai
sudo systemctl enable printer-ai-ui

echo "=============================="
echo " Installation Complete!"
echo "=============================="

echo "Start services with:"
echo "  sudo systemctl start printer-ai"
echo "  sudo systemctl start printer-ai-ui"

echo "Web UI:"
echo "  http://<your-ip>:5000"