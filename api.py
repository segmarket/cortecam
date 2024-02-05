from flask import Flask, request, jsonify, Response
import subprocess
import datetime
import cv2
import os
from threading import Thread
import numpy as np

app = Flask(__name__)

# Dicionário para armazenar os processos de gravação
recording_processes = {}

# Dicionário para armazenar os links RTSP configurados para cada mercado
streams = {}


def create_recording_directory(market_name):
    base_dir = "recordings"
    market_dir = os.path.join(base_dir, market_name)
    os.makedirs(market_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    recording_dir = os.path.join(market_dir, timestamp)
    os.makedirs(recording_dir, exist_ok=True)
    return recording_dir

def start_recording(rtsp_link, recording_dir, camera_index):
    filename = f"{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_camera_{camera_index}.mp4"
    filepath = os.path.join(recording_dir, filename)
    process = subprocess.Popen(['ffmpeg', '-i', rtsp_link, '-c', 'copy', filepath])
    return process

def stop_recording(process):
    process.terminate()
    process.wait()

def detect_movement(frame1, frame2):
    gray1 = cv2.cvtColor(frame1, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(gray1, gray2)
    _, thresh = cv2.threshold(cv2.blur(diff, (5, 5)), 20, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return any(cv2.contourArea(contour) > 500 for contour in contours)

def apply_ignore_area_mask(frame, ignore_area):
    mask = np.zeros(frame.shape[:2], dtype="uint8")
    cv2.rectangle(mask, (ignore_area[0], ignore_area[1]), (ignore_area[0] + ignore_area[2], ignore_area[1] + ignore_area[3]), 255, -1)
    return cv2.bitwise_and(frame, frame, mask=mask)

def monitor_and_record(rtsp_links, market_name, ignore_area):
    recording_dir = create_recording_directory(market_name)
    caps = [cv2.VideoCapture(rtsp_link) for rtsp_link in rtsp_links]
    prev_frames = [None] * len(rtsp_links)
    recording_flags = [False] * len(rtsp_links)
    recording_processes = [None] * len(rtsp_links)

    while True:
        for i, cap in enumerate(caps):
            ret, frame = cap.read()
            if not ret or frame is None:
                continue

            frame = apply_ignore_area_mask(frame, ignore_area)

            if prev_frames[i] is not None:
                movement_detected = detect_movement(prev_frames[i], frame)

                if movement_detected and not recording_flags[i]:
                    print(f"Movimento detectado na câmera {i+1}. Iniciando a gravação.")
                    recording_processes[i] = start_recording(rtsp_links[i], recording_dir, i+1)
                    recording_flags[i] = True
                elif not movement_detected and recording_flags[i]:
                    print(f"Nenhum movimento detectado na câmera {i+1} por um tempo. Parando a gravação.")
                    stop_recording(recording_processes[i])
                    recording_flags[i] = False
                    recording_processes[i] = None

            prev_frames[i] = frame

    for cap in caps:
        cap.release()
    for process in recording_processes:
        if process:
            stop_recording(process)


def check_stream(rtsp_link):
    """Verifica se um stream RTSP está acessível."""
    cap = cv2.VideoCapture(rtsp_link)
    ret, _ = cap.read()
    cap.release()
    return ret

@app.route('/configure', methods=['POST'])
def configure():
    data = request.json
    market_name = data.get('market_name')
    rtsp_links = data.get('rtsp_links')
    ignore_area = data.get('ignore_area')  # Exemplo: ignore_area = [x, y, width, height]

    if not market_name or not rtsp_links or not ignore_area:
        return jsonify({'error': 'Dados ausentes ou inválidos'}), 400

    if not isinstance(rtsp_links, list) or len(rtsp_links) < 1:
        return jsonify({'error': 'Links RTSP inválidos fornecidos. São necessários ao menos 1 link.'}), 400

    if not isinstance(ignore_area, (list, tuple)) or len(ignore_area) != 4:
        return jsonify({'error': 'Invalid ignore_area format. It must be a list or tuple with four elements (x, y, width, height).'}), 400

    ignore_area = tuple(ignore_area)

    # Armazena as configurações no dicionário global `streams`
    streams[market_name] = {
        'rtsp_links': rtsp_links,
        'ignore_area': ignore_area
    }

    # Inicie a thread de monitoramento e gravação com os parâmetros corretos
    Thread(target=monitor_and_record, args=(rtsp_links, market_name, ignore_area)).start()
    return jsonify({'message': 'Configuração bem-sucedida'}), 200

@app.route('/stream/<market_name>/<int:stream_index>')
def stream_market(market_name, stream_index):
    market_name = market_name.lower()  # Converte para minúsculas
    if market_name not in streams:
        return jsonify({'error': 'Mercado não encontrado ou não configurado'}), 404

    # Valida se o índice do stream é válido
    if stream_index < 1 or stream_index > len(streams[market_name]['rtsp_links']):
        return jsonify({'error': 'Índice do stream é válido'}), 404

    # Acessa o link RTSP específico com base no índice fornecido (ajustado para base 0)
    rtsp_link = streams[market_name]['rtsp_links'][stream_index - 1]
    return Response(generate_video_stream(rtsp_link), mimetype='multipart/x-mixed-replace; boundary=frame')

def generate_video_stream(rtsp_link):
    """
    Gera o stream de vídeo a partir do link RTSP fornecido.
    """
    cap = cv2.VideoCapture(rtsp_link)
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        # Codifica o frame em formato JPEG
        ret, buffer = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        # Converte o frame codificado em bytes e o envia como parte da resposta multipart
        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

if __name__ == '__main__':
    app.run(threaded=True)
