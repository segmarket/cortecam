from flask import Flask, request, jsonify, Response
import subprocess
import datetime
import cv2
import os
import numpy as np
from threading import Thread, Event
import time
import base64
from azure.storage.blob import BlobServiceClient, BlobClient

app = Flask(__name__)

recording_processes = {}
streams = {}
market_threads = {}
last_movement_time = {}
# Configurações do Azure Blob Storage
CONNECTION_STRING = 'DefaultEndpointsProtocol=https;AccountName=segcam;AccountKey=js1kpBrA+mEufFg/FoN6lgoCpOiSNnyNFeKNy0IcN+GYnj/wX4AO9DLFK6Od3i8BW3VzQ9FR4Zrq+ASt5A5vqA==;EndpointSuffix=core.windows.net'
CONTAINER_NAME = 'armazenamento'



def upload_file_to_blob_storage(connection_string, container_name, file_path, blob_name):
    try:
        blob_service_client = BlobServiceClient.from_connection_string(connection_string)
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
        with open(file_path, "rb") as data:
            blob_client.upload_blob(data, overwrite=True)
        print(f"Arquivo {file_path} foi carregado com sucesso para o blob {blob_name} no container {container_name}.")
    except Exception as e:
        print(f"Erro ao fazer upload do arquivo: {e}")


def start_recording(rtsp_link, market_name, camera_index):
    recording_dir = create_recording_directory(market_name)
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    filename = f"{timestamp}_camera_{camera_index}.mp4"
    filepath = os.path.join(recording_dir, filename)
    blob_name = f"{market_name}/{timestamp}_camera_{camera_index}.mp4"

    # Inicie a gravação com FFmpeg
    process = subprocess.Popen(['ffmpeg', '-i', rtsp_link, '-c', 'copy', filepath], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    return process, filepath, blob_name

def stop_recording(process, filepath, blob_name):
    process.terminate()
    process.wait()
    # Certifique-se de que esta chamada está passando os argumentos corretamente
    upload_file_to_blob_storage(CONNECTION_STRING, CONTAINER_NAME, filepath, blob_name)
    # Considere remover o arquivo local após o upload, se desejado
    os.remove(filepath)



def create_recording_directory(market_name):
    base_dir = os.path.join("recordings", market_name)
    if not os.path.exists(base_dir):
        os.makedirs(base_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    recording_dir = os.path.join(base_dir, timestamp)
    os.makedirs(recording_dir, exist_ok=True)
    return recording_dir

def detect_movement(frame1, frame2, sensibilidade=100):
    gray1 = cv2.cvtColor(frame1, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(gray1, gray2)
    _, thresh = cv2.threshold(cv2.blur(diff, (5, 5)), 20, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return any(cv2.contourArea(contour) > sensibilidade for contour in contours)

def apply_ignore_area_mask(frame, ignore_area):
    # Ignora as bordas inferiores e laterais onde a data e a hora são exibidas
    height, width = frame.shape[:2]
    bottom_ignore_height = 50  # A altura da parte inferior para ignorar
    side_ignore_width = 20     # A largura das bordas laterais para ignorar

    mask = np.ones(frame.shape[:2], dtype="uint8") * 255
    cv2.rectangle(mask, (0, height - bottom_ignore_height), (width, height), 0, -1)
    cv2.rectangle(mask, (0, 0), (side_ignore_width, height), 0, -1)
    cv2.rectangle(mask, (width - side_ignore_width, 0), (width, height), 0, -1)

    masked_frame = cv2.bitwise_and(frame, frame, mask=mask)
    return masked_frame

def monitor_and_record(rtsp_links, market_name, ignore_area, stop_event, sensibilities):
    global last_movement_time
    caps = [cv2.VideoCapture(link) for link in rtsp_links]
    prev_frames = [None] * len(rtsp_links)
    recording_dir = ""

    # Inicializa o último tempo de movimento para agora para todas as câmeras
    for i in range(len(rtsp_links)):
        last_movement_time[i] = time.time()

    while not stop_event.is_set():
        for i, cap in enumerate(caps):
            ret, frame = cap.read()
            if not ret:
                continue

            frame = apply_ignore_area_mask(frame, ignore_area)
            if prev_frames[i] is not None:
                if detect_movement(prev_frames[i], frame, sensibilities[i]):
                    last_movement_time[i] = time.time()  # Atualiza o tempo do último movimento
                    if not recording_dir:
                        recording_dir = create_recording_directory(market_name)
                    if not recording_processes.get(i):
                        print(f"Movimento detectado na câmera {i+1}. Iniciando a gravação.")
                        process, filepath, blob_name = start_recording(rtsp_links[i], recording_dir, i+1)
                        recording_processes[i] = {
                            "process": process,
                            "filepath": filepath,
                            "blob_name": blob_name
                        }

                else:
                    # Verifica se passou o tempo limite desde a última detecção de movimento
                    if time.time() - last_movement_time[i] > 20:  #20 segundos
                        if recording_processes.get(i):
                            print(f"Nenhum movimento detectado na câmera {i+1} por um tempo. Parando a gravação.")
                            process_info = recording_processes[i]
                            stop_recording(process_info["process"], process_info["filepath"], process_info["blob_name"])
                            recording_processes[i] = None

            prev_frames[i] = frame.copy()

    for cap in caps:
        cap.release()

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
    ignore_area = data.get('ignore_area', [])
    container_name = data.get('container_name', 'armazenamento')
    sensibilities = data.get('sensibilities', [100] * len(rtsp_links))  # Default sensibilidade

    if not market_name or not rtsp_links or len(rtsp_links) != len(sensibilities):
        return jsonify({'error': 'Erro: falta parâmetros ou parâmetros inconsistentes'}), 400

    is_update = market_name in streams
    streams[market_name] = {'rtsp_links': rtsp_links, 'ignore_area': ignore_area, 'container_name': container_name, 'sensibilities': sensibilities}

    if market_name in market_threads:
        market_threads[market_name]['stop_event'].set()
        market_threads[market_name]['thread'].join()
        del market_threads[market_name]

    stop_event = Event()
    new_thread = Thread(target=monitor_and_record, args=(rtsp_links, market_name, ignore_area, stop_event, sensibilities))
    new_thread.start()
    market_threads[market_name] = {'thread': new_thread, 'stop_event': stop_event}

    return jsonify({'message': 'Configuração atualizada com sucesso' if is_update else 'Configuração criada com sucesso'}), 200


@app.route('/stream/<market_name>/<int:stream_index>')
def stream_market(market_name, stream_index):
    market_name = market_name.lower()
    if market_name not in streams:
        return jsonify({'error': 'Mercado não encontrado ou não configurado'}), 404

    if stream_index < 1 or stream_index > len(streams[market_name]['rtsp_links']):
        return jsonify({'error': 'Índice do stream inválido'}), 404

    rtsp_link = streams[market_name]['rtsp_links'][stream_index - 1]
    # Retorna apenas o link RTSP para o cliente
    return jsonify({'rtsp_link': rtsp_link}), 200

@app.route('/delete/<market_name>', methods=['DELETE'])
def delete_configuration(market_name):
    # Verifica se o mercado existe antes de tentar deletar
    if market_name in streams:
        del streams[market_name]
        # Aqui você também pode querer parar a monitoração para esse mercado, se aplicável
        return jsonify({'message': 'Configuração deletada com sucesso!'}), 200
    else:
        return jsonify({'error': 'Mercado não encontrado'}), 404


if __name__ == '__main__':
    port = os.getenv('PORT', '80')  # Porta definida pelo Azure ou a porta 80 por padrão
    app.run(host='0.0.0.0', port=int(port), debug=False)
