from flask import Flask, request, jsonify, Response
import subprocess
import datetime
import cv2
import os
from threading import Thread
import numpy as np
from dotenv import load_dotenv
import time
from concurrent.futures import ThreadPoolExecutor
import base64

app = Flask(__name__)

# Dicionário para armazenar os processos de gravação
recording_processes = {}

# Dicionário para armazenar os links RTSP configurados para cada mercado
streams = {}

# Carrega variáveis de ambiente do arquivo .env
load_dotenv('chave.env')

def detect_movement(frame1, frame2):
    """Detecta movimento comparando dois frames."""
    gray1 = cv2.cvtColor(frame1, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(gray1, gray2)
    _, thresh = cv2.threshold(cv2.blur(diff, (5, 5)), 20, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return any(cv2.contourArea(contour) > 500 for contour in contours)

def start_recording_and_upload(rtsp_link, container_name, market_name, camera_index):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    local_file_name = f"{timestamp}_camera_{camera_index}.mp4"
    #blob_name = f"{market_name}/{camera_index}/{local_file_name}"

    # Gravação temporária local
    command = ['ffmpeg', '-i', rtsp_link, '-t', '30', '-y', local_file_name]  # Grava por 30 segundos
    subprocess.run(command, check=True)

    # Upload para o Blob Storage
    # upload_file_to_blob(local_file_name, container_name, blob_name)

def stop_recording(process):
    process.terminate()
    process.wait()

def apply_ignore_area_mask(frame, ignore_area):
    mask = np.zeros(frame.shape[:2], dtype="uint8")
    cv2.rectangle(mask, (ignore_area[0], ignore_area[1]), (ignore_area[0] + ignore_area[2], ignore_area[1] + ignore_area[3]), 255, -1)
    return cv2.bitwise_and(frame, frame, mask=mask)

def monitor_and_record(rtsp_links, market_name, ignore_area, container_name):
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
                    recording_processes[i] = start_recording_and_upload(rtsp_links[i], container_name, market_name, i+1)
                    recording_flags[i] = True
                elif not movement_detected and recording_flags[i]:
                    print(f"Nenhum movimento detectado na câmera {i+1} por um tempo. Parando a gravação.")
                    stop_recording(recording_processes[i])
                    recording_flags[i] = False
                    recording_processes[i] = None

            prev_frames[i] = frame

@app.route('/configure', methods=['POST'])
def configure():
    data = request.json
    market_name = data.get('market_name')
    rtsp_links = data.get('rtsp_links')
    ignore_area = data.get('ignore_area', [0, 0, 0, 0])  # Default to full frame if not provided
    container_name = data.get('container_name', 'default-container')  # Default container name

    if not market_name or not rtsp_links:
        return jsonify({'error': 'Missing required data'}), 400

    streams[market_name] = {'rtsp_links': rtsp_links, 'ignore_area': ignore_area}
    Thread(target=monitor_and_record, args=(rtsp_links, market_name, ignore_area, container_name)).start()
    return jsonify({'message': 'Configuration successful'}), 200

def generate_video_stream(rtsp_link, width=640, height=480, frame_reduction_factor=4):
    """
    Gera o stream de vídeo a partir do link RTSP fornecido com redução de taxa de frames,
    ajustando a resolução do vídeo conforme especificado e aplicando a redução da taxa de frames.
    """
    cap = cv2.VideoCapture(rtsp_link)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    frame_count = 0
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        frame_count += 1
        if frame_count % frame_reduction_factor != 0:
            continue
        frame = cv2.resize(frame, (width, height))
        _, buffer = cv2.imencode('.jpg', frame)
        frame_base64 = base64.b64encode(buffer).decode('utf-8')
        yield f"data: {frame_base64}\n\n"
        time.sleep(1 / 30)  # Ajuste a taxa de frames conforme necessário

@app.route('/stream/<market_name>/<int:stream_index>')
def stream_market(market_name, stream_index):
    market_name = market_name.lower()
    if market_name not in streams:
        return jsonify({'error': 'Mercado não encontrado ou não configurado'}), 404

    if stream_index < 1 or stream_index > len(streams[market_name]['rtsp_links']):
        return jsonify({'error': 'Índice do stream inválido'}), 404

    rtsp_link = streams[market_name]['rtsp_links'][stream_index - 1]
    return Response(generate_video_stream(rtsp_link, 640, 480, 4), mimetype='text/event-stream')

if __name__ == '__main__':
    port = os.getenv('PORT', '8080')  # Porta definida pelo Azure ou a porta 80 por padrão
    app.run(host='0.0.0.0', port=int(port), debug=False)
