import cv2
import numpy as np
import tensorflow as tf
import time
import threading
import serial
from collections import deque
from flask import Flask, render_template_string, Response, request, jsonify

app = Flask(__name__)

# ================= CONFIG =================
ESP32_STREAM = "http://192.168.1.163:81/stream" 
MODEL_PATH = "fire_detection_model.tflite"
SERIAL_PORT = "COM7"

CONF_THRESHOLD = 0.65
FIRE_CONFIRM = 4
SAFE_CONFIRM = 5
FIRE_COLOR_THRESHOLD = 0.005

# ================= GLOBAL VARIABLES =================
current_status = "ĐANG KHỞI ĐỘNG..."
current_conf = 0.0
current_fps = 0
current_fire_ratio = 0.0
current_gas = 0

sys_mode = "AUTO"  
device_states = {"led": 0, "buzzer": 0, "pump": 0}

global_frame = None
is_running = True
frame_lock = threading.Lock()

# ================= KHỞI TẠO AI =================
try:
    interpreter = tf.lite.Interpreter(model_path=MODEL_PATH)
    interpreter.allocate_tensors()
    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()
except Exception as e:
    print(f"Lỗi nạp AI: {e}")
    exit()

def predict_fire(frame):
    try:
        img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (224, 224))
        if input_details[0]["dtype"] == np.float32:
            img = img.astype(np.float32) / 255.0
        else:
            img = img.astype(np.uint8)
        img = np.expand_dims(img, axis=0)
        interpreter.set_tensor(input_details[0]["index"], img)
        interpreter.invoke()
        pred = interpreter.get_tensor(output_details[0]["index"])[0]
        return float(pred) if np.isscalar(pred) else float(pred[0] if len(pred)==1 else pred[1])
    except:
        return 0.0
    
def fire_color_ratio(frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([0, 80, 180]), np.array([35, 255, 255]))
    return cv2.countNonZero(mask) / (frame.shape[0] * frame.shape[1])

# ================= LUỒNG XỬ LÝ (CAMERA + AI) =================
def video_capture_thread():
    global global_frame, is_running
    cap = cv2.VideoCapture(ESP32_STREAM, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    while is_running:
        ret, frame = cap.read()
        if not ret or frame is None:
            cap.release()
            time.sleep(0.5)
            cap = cv2.VideoCapture(ESP32_STREAM, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            continue
        with frame_lock: global_frame = frame.copy()
    cap.release()

def ai_worker_thread():
    global global_frame, current_status, current_conf, current_fps, current_fire_ratio, is_running
    history = deque(maxlen=10)
    fire_count, safe_count, fire_state = 0, 0, False
    fps_time = time.time()
    
    while is_running:
        start_time = time.time()
        frame = None
        with frame_lock:
            if global_frame is not None: frame = global_frame.copy()
                
        if frame is not None:
            avg_conf = sorted(history)[len(history)//2] if history else 0
            fire_ratio = fire_color_ratio(frame)
            history.append(predict_fire(frame))

            if avg_conf > CONF_THRESHOLD and fire_ratio > FIRE_COLOR_THRESHOLD:
                fire_count += 1; safe_count = 0
            else:
                safe_count += 1; fire_count = 0

            if fire_count >= FIRE_CONFIRM: fire_state = True
            if safe_count >= SAFE_CONFIRM: fire_state = False

            current_conf = avg_conf
            current_fire_ratio = fire_ratio
            current_status = "HỎA HOẠN !!!" if fire_state else "AN TOÀN"
            current_fps = int(1 / max(time.time() - fps_time, 0.0001))
            fps_time = time.time()

        time.sleep(max(0, (1.0 / 8.0) - (time.time() - start_time)))

# ================= LUỒNG SERIAL GIAO TIẾP ARDUINO =================
def serial_worker_thread():
    global current_status, current_gas, sys_mode, device_states, is_running
    try:
        ser = serial.Serial(SERIAL_PORT, 9600, timeout=0.1)
        time.sleep(2) 
        
        last_sent_cmd = b""
        last_send_time = 0

        while is_running:
            # 1. QUYẾT ĐỊNH LỆNH CẦN GỬI
            if sys_mode == "MANUAL":
                cmd = f"CTRL:{device_states['led']},{device_states['buzzer']},{device_states['pump']}\n".encode()
            else:
                if "HỎA HOẠN" in str(current_status):
                    cmd = b"FIRE\n"
                else:
                    cmd = b"SAFE\n"
            
            # 2. CHỈ GỬI KHI TRẠNG THÁI THAY ĐỔI HOẶC ĐỊNH KỲ 1 GIÂY (Chống nghẽn bộ nhớ)
            current_time = time.time()
            if cmd != last_sent_cmd or (current_time - last_send_time > 1.0):
                ser.write(cmd)
                ser.flush()  # Ép cáp USB đẩy dữ liệu đi ngay lập tức
                print(f"[{time.strftime('%H:%M:%S')}] PYTHON ĐÃ GỬI LỆNH: {cmd.decode().strip()}")
                last_sent_cmd = cmd
                last_send_time = current_time

            # 3. ĐỌC DỮ LIỆU TỪ ARDUINO GỬI LÊN
            while ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line.startswith("GAS:"):
                    try: current_gas = int(line.split(":")[1])
                    except: pass
                elif line.startswith("STATE:"):
                    try:
                        states = line.split(":")[1].split(",")
                        device_states['led'] = int(states[0])
                        device_states['buzzer'] = int(states[1])
                        device_states['pump'] = int(states[2])
                    except: pass
            time.sleep(0.05) 
    except Exception as e:
        print("Lỗi Serial:", e)

# ================= FLASK GIAO DIỆN WEB =================
def generate_frames():
    global global_frame, current_status
    while True:
        frame = None
        with frame_lock:
            if global_frame is not None: frame = global_frame.copy()
        if frame is None:
            time.sleep(0.01)
            continue
        
        color = (0, 0, 255) if "HỎA HOẠN" in current_status else (0, 255, 0)
        cv2.putText(frame, f"STATUS: {current_status}", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ret: yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

@app.route('/')
def index():
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>AIoT Fire Dashboard</title>
        <style>
            :root { --bg: #0f172a; --card: #1e293b; --text: #e2e8f0; --accent: #38bdf8; --danger: #ef4444; --warning: #f59e0b; --success: #10b981; }
            * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
            body { background: var(--bg); color: var(--text); padding: 20px; }
            .header { text-align: center; margin-bottom: 25px; text-transform: uppercase; letter-spacing: 2px; color: var(--accent); }
            .dashboard { display: grid; grid-template-columns: 1fr; gap: 20px; max-width: 1000px; margin: auto; }
            @media (min-width: 768px) { .dashboard { grid-template-columns: 1.5fr 1fr; } }
            .card { background: var(--card); border-radius: 16px; padding: 20px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); border: 1px solid rgba(255,255,255,0.05); }
            .card h3 { margin-bottom: 15px; font-size: 1.1rem; color: #94a3b8; border-bottom: 1px solid #334155; padding-bottom: 10px; }
            .video-container { width: 100%; border-radius: 12px; overflow: hidden; border: 2px solid #334155; background: #000; position: relative; }
            .video-container img { width: 100%; display: block; }
            .status-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 20px; }
            .badge { padding: 15px; border-radius: 12px; text-align: center; font-weight: bold; font-size: 1.2rem; background: #334155; transition: 0.3s; }
            .badge span { display: block; font-size: 0.8rem; color: #cbd5e1; font-weight: normal; margin-bottom: 5px; text-transform: uppercase; }
            .b-safe { color: var(--success); box-shadow: inset 0 0 10px rgba(16, 185, 129, 0.2); }
            .b-fire { color: white; background: var(--danger); animation: pulse 1s infinite; }
            .b-gas-ok { color: var(--success); }
            .b-gas-warn { color: white; background: var(--warning); animation: pulse 1s infinite;}
            .control-group { display: flex; justify-content: space-between; align-items: center; padding: 12px 0; border-bottom: 1px solid #334155; }
            .control-group:last-child { border: none; }
            .toggle-label { font-size: 1rem; font-weight: 500; }
            .switch { position: relative; display: inline-block; width: 50px; height: 26px; }
            .switch input { opacity: 0; width: 0; height: 0; }
            .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #475569; transition: .4s; border-radius: 34px; }
            .slider:before { position: absolute; content: ""; height: 18px; width: 18px; left: 4px; bottom: 4px; background-color: white; transition: .4s; border-radius: 50%; }
            input:checked + .slider { background-color: var(--accent); }
            input:disabled:not(:checked) + .slider { opacity: 0.5; }
            input:checked + .slider:before { transform: translateX(24px); }
            .ai-stats { font-size: 0.85rem; color: #64748b; margin-top: 15px; text-align: center; }
            @keyframes pulse { 0% { transform: scale(1); } 50% { transform: scale(1.02); } 100% { transform: scale(1); } }
        </style>
    </head>
    <body>
        <h2 class="header">Hệ thống Giám sát & Chữa cháy AIoT</h2>
        <div class="dashboard">
            <div class="card">
                <h3>Trực tiếp Camera (ESP32-CAM)</h3>
                <div class="video-container"><img src="/video_feed" alt="Camera Feed"></div>
                <div class="ai-stats" id="ai-details">Chờ dữ liệu AI...</div>
            </div>
            <div>
                <div class="status-grid">
                    <div id="fire-badge" class="badge b-safe"><span>Trạng thái Lửa</span>AN TOÀN</div>
                    <div id="gas-badge" class="badge b-gas-ok"><span>Khí Gas (MQ-2)</span>BÌNH THƯỜNG</div>
                </div>
                <div class="card">
                    <h3>Trạng Thái Thiết Bị Thực Tế</h3>
                    <div class="control-group">
                        <span class="toggle-label" style="color: var(--accent)">CHẾ ĐỘ THỦ CÔNG</span>
                        <label class="switch">
                            <input type="checkbox" id="modeToggle" onchange="toggleMode()">
                            <span class="slider"></span>
                        </label>
                    </div>
                    <div id="manual-controls">
                        <div class="control-group">
                            <span class="toggle-label">Đèn Cảnh Báo (LED)</span>
                            <label class="switch"><input type="checkbox" id="ledToggle" disabled onchange="sendControl()"><span class="slider"></span></label>
                        </div>
                        <div class="control-group">
                            <span class="toggle-label">Còi Báo Động (Buzzer)</span>
                            <label class="switch"><input type="checkbox" id="buzToggle" disabled onchange="sendControl()"><span class="slider"></span></label>
                        </div>
                        <div class="control-group">
                            <span class="toggle-label">Máy Bơm Nước (Relay)</span>
                            <label class="switch"><input type="checkbox" id="pumpToggle" disabled onchange="sendControl()"><span class="slider"></span></label>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        <script>
            setInterval(() => {
                fetch('/api/status')
                    .then(res => res.json())
                    .then(data => {
                        const fb = document.getElementById("fire-badge");
                        if(data.status.includes("HỎA HOẠN")) {
                            fb.className = "badge b-fire"; fb.innerHTML = "<span>Trạng thái Lửa</span>CÓ CHÁY!";
                        } else {
                            fb.className = "badge b-safe"; fb.innerHTML = "<span>Trạng thái Lửa</span>AN TOÀN";
                        }
                        const gb = document.getElementById("gas-badge");
                        if(data.gas > 400) { 
                            gb.className = "badge b-gas-warn"; gb.innerHTML = `<span>Khí Gas (MQ-2)</span>RÒ RỈ (${data.gas})`;
                        } else {
                            gb.className = "badge b-gas-ok"; gb.innerHTML = `<span>Khí Gas (MQ-2)</span>BÌNH THƯỜNG (${data.gas})`;
                        }
                        document.getElementById("ai-details").innerText = `Độ tin cậy AI: ${data.conf} | Tốc độ: ${data.fps} FPS`;
                        
                        const isManual = data.mode === "MANUAL";
                        document.getElementById('modeToggle').checked = isManual;
                        document.getElementById('ledToggle').disabled = !isManual;
                        document.getElementById('buzToggle').disabled = !isManual;
                        document.getElementById('pumpToggle').disabled = !isManual;

                        document.getElementById('ledToggle').checked = data.devices.led === 1;
                        document.getElementById('buzToggle').checked = data.devices.buzzer === 1;
                        document.getElementById('pumpToggle').checked = data.devices.pump === 1;
                    });
            }, 500);

            function toggleMode() {
                const isManual = document.getElementById('modeToggle').checked;
                sendControl();
            }

            function sendControl() {
                const payload = {
                    mode: document.getElementById('modeToggle').checked ? "MANUAL" : "AUTO",
                    led: document.getElementById('ledToggle').checked ? 1 : 0,
                    buzzer: document.getElementById('buzToggle').checked ? 1 : 0,
                    pump: document.getElementById('pumpToggle').checked ? 1 : 0
                };
                fetch('/api/control', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
            }
        </script>
    </body>
    </html>
    """)

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/status')
def status():
    return jsonify({
        "status": current_status, 
        "conf": round(current_conf, 2), 
        "fps": current_fps, 
        "gas": current_gas,
        "mode": sys_mode,
        "devices": device_states
    })

@app.route('/api/control', methods=['POST'])
def control():
    global sys_mode, device_states
    data = request.json
    sys_mode = data.get('mode', sys_mode)
    device_states['led'] = data.get('led', device_states['led'])
    device_states['buzzer'] = data.get('buzzer', device_states['buzzer'])
    device_states['pump'] = data.get('pump', device_states['pump'])
    return jsonify({"msg": "ok"})

if __name__ == '__main__':
    threading.Thread(target=video_capture_thread, daemon=True).start()
    threading.Thread(target=ai_worker_thread, daemon=True).start()
    threading.Thread(target=serial_worker_thread, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)