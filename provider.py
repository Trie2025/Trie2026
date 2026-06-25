"""
摄像头提供端（学生机）
- 启动后最小化到系统托盘，无弹窗
- 在授权局域网内广播自身，等待教师查看端连接
- 被连接后推送摄像头 MJPEG 流
- 支持查看端调节清晰度/分辨率
- 查看端可发送正常停止指令，摄像头端会关闭当前连接但保持程序运行
"""

import argparse
import cv2
import json
import queue
import socket
import struct
import threading
import time

import pystray
from PIL import Image, ImageDraw


DISCOVERY_PORT = 50001      # UDP 广播端口
STREAM_PORT = 50000         # TCP 推流/控制端口
BROADCAST_INTERVAL = 3.0    # 广播间隔（秒）
DEFAULT_QUALITY = 70
DEFAULT_RESOLUTION = (1280, 720)

running = True
clients = []
status_text = "等待连接"
clients_lock = threading.Lock()


def create_tray_icon():
    """生成一个简单的摄像头形状托盘图标。"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 14, 60, 50], fill="#4A90D9", outline="#2C5A8C", width=3)
    draw.ellipse([22, 22, 42, 42], fill="#FFFFFF")
    draw.rectangle([48, 22, 60, 42], fill="#4A90D9", outline="#2C5A8C", width=2)
    return img


def get_ip():
    """获取本机局域网 IP。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def allowed_client(addr, allowed_subnet):
    """简单子网过滤：仅允许同一 /24 网段连接。"""
    if not allowed_subnet:
        return True
    return addr.startswith(allowed_subnet)


def recvall(sock, n):
    """可靠读取 n 字节。"""
    data = b""
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data += packet
    return data


def command_reader(sock, cmd_queue):
    """在独立线程中读取查看端控制指令。"""
    while running:
        try:
            len_bytes = recvall(sock, 4)
            if not len_bytes:
                break
            msg_len = struct.unpack("!I", len_bytes)[0]
            msg_data = recvall(sock, msg_len)
            if msg_data is None:
                break
            cmd = json.loads(msg_data.decode("utf-8"))
            cmd_queue.put(cmd)
        except Exception:
            break


def handle_client(conn, addr, allowed_subnet):
    """处理单个查看端连接。"""
    global status_text
    if not allowed_client(addr[0], allowed_subnet):
        conn.close()
        return

    with clients_lock:
        clients.append(addr)
        status_text = f"连接数: {len(clients)}"

    cmd_queue = queue.Queue()
    reader = threading.Thread(target=command_reader, args=(conn, cmd_queue), daemon=True)
    reader.start()

    quality = DEFAULT_QUALITY
    width, height = DEFAULT_RESOLUTION
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, 30)

    try:
        while running and reader.is_alive():
            # 处理累积的控制指令
            while not cmd_queue.empty():
                try:
                    cmd = cmd_queue.get_nowait()
                    if cmd.get("cmd") == "quality":
                        quality = max(10, min(95, int(cmd.get("value", quality))))
                    elif cmd.get("cmd") == "resolution":
                        try:
                            w, h = cmd.get("value", "1280x720").split("x")
                            width, height = int(w), int(h)
                            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                        except Exception:
                            pass
                    elif cmd.get("cmd") == "stop":
                        return
                except queue.Empty:
                    break

            ret, frame = cap.read()
            if not ret:
                time.sleep(0.01)
                continue

            if frame.shape[1] != width or frame.shape[0] != height:
                frame = cv2.resize(frame, (width, height))

            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if not ok:
                continue

            data = encoded.tobytes()
            try:
                conn.sendall(struct.pack("!I", len(data)) + data)
            except Exception:
                break
    finally:
        cap.release()
        try:
            conn.close()
        except Exception:
            pass
        with clients_lock:
            if addr in clients:
                clients.remove(addr)
            status_text = f"连接数: {len(clients)}" if clients else "等待连接"


def discovery_broadcaster(ip, port):
    """UDP 广播自身信息，便于查看端自动发现。"""
    hostname = socket.gethostname()
    msg = f"CAM_PROVIDER|{ip}|{port}|{hostname}".encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while running:
        try:
            sock.sendto(msg, ("<broadcast>", DISCOVERY_PORT))
        except Exception:
            pass
        time.sleep(BROADCAST_INTERVAL)
    sock.close()


def stream_server(ip, allowed_subnet):
    """TCP 服务器：接受查看端连接并启动推流线程。"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", STREAM_PORT))
    server.listen(5)
    server.settimeout(1.0)

    while running:
        try:
            conn, addr = server.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr, allowed_subnet), daemon=True)
            t.start()
        except socket.timeout:
            continue
        except Exception:
            break
    server.close()


def setup_tray(icon):
    icon.visible = True


def exit_app(icon):
    global running
    running = False
    icon.stop()


def tooltip_updater(icon):
    """定期更新托盘图标提示文本，显示当前连接状态。"""
    ip = get_ip()
    while running:
        with clients_lock:
            text = f"摄像头提供端\nIP: {ip}\n端口: {STREAM_PORT}\n{status_text}"
        try:
            icon.title = text
        except Exception:
            pass
        time.sleep(1.0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--subnet", default="", help="仅允许该子网开头的 IP 连接，例如 192.168.1")
    args = parser.parse_args()

    ip = get_ip()

    threading.Thread(target=stream_server, args=(ip, args.subnet), daemon=True).start()
    threading.Thread(target=discovery_broadcaster, args=(ip, STREAM_PORT), daemon=True).start()

    menu = pystray.Menu(
        pystray.MenuItem("退出", exit_app),
    )
    icon = pystray.Icon("cam_provider", create_tray_icon(), f"摄像头提供端 ({ip})", menu)
    threading.Thread(target=tooltip_updater, args=(icon,), daemon=True).start()
    icon.run(setup=setup_tray)


if __name__ == "__main__":
    main()
