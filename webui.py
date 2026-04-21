from flask import Flask, render_template, request, jsonify
import json
import requests

app = Flask(__name__)
CONFIG_PATH = "config.json"
PRINTER_IP = "127.0.0.1"  # Moonraker usually runs on localhost

def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

@app.route("/")
def index():
    return render_template("index.html", cfg=load_config())

@app.route("/update", methods=["POST"])
def update():
    cfg = load_config()
    data = request.json
    # Deep update for nested dictionaries (like calibration)
    for k, v in data.items():
        if isinstance(v, dict) and k in cfg:
            cfg[k].update(v)
        else:
            cfg[k] = v
    save_config(cfg)
    return "OK"

@app.route("/gcode", methods=["POST"])
def send_gcode():
    command = request.json.get("command")
    try:
        # Klipper/Moonraker API call
        url = f"http://{PRINTER_IP}:7125/printer/gcode/script?script={command}"
        response = requests.post(url)
        return response.text
    except Exception as e:
        return str(e), 500

@app.route("/debug")
def debug():
    try:
        return open("/tmp/ai_debug.jpg", "rb").read()
    except FileNotFoundError:
        return "Image not found", 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
